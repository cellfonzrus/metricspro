"""Core / RBAC router — role management + employee account provisioning.

Powers the Role Assignment module (migration 015). Roles live in storeops.roles with an
editable permissions JSONB; the logged-in identity lives in storeops.app_users (linked to a
Supabase Auth account). Everything is in the `storeops` schema because that schema is already
exposed to PostgREST (the supabase-py client talks to PostgREST, which only serves exposed
schemas — `core` is not exposed). Employees authenticate with email+password; this router
creates the auth accounts (service key) and assigns roles. The frontend never reads these
tables directly — it calls the token-verified /core/me — so the tables stay backend-only (RLS
with no anon policy; the service role bypasses RLS).
"""
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Header, Request, BackgroundTasks
from app.core.database import get_supabase, get_supabase_admin
from app.core.config import settings
from app.modules.core.entitlements import (
    MODULE_CATALOG, ROLE_GATE_KEYS, load_module_catalog,
    sync_tenant, sync_all_tenants, needs_sync, SEED_VERSION,
)
# Auth-hardening (2026-07-17): PURE password-policy / OTP / 2FA-marker helpers (unit-proven) + the
# delivery bridge that reuses notify's Resend/WhatsApp creds logic (no duplication).
from app.modules.core import auth_security as _sec
from app.modules.core import auth_notify as _anotify
# Canonical tenant-membership primitives — the ONE rule for "which tenant is this login acting as",
# shared so every module (e.g. storeops._caller_identity) resolves it identically (no drift).
from app.modules.core.membership import (
    list_memberships as _memberships,
    pick_membership as _pick_membership,
)
# Canonical ACCESS-SCOPE primitives (REPORTING span vs SCHEDULING reach + the ONE market
# universe that both the grant PICKER and the grant RESOLVER read). See app/core/scope.py.
from app.core import scope as _scope

router = APIRouter(prefix="/core", tags=["Core / RBAC"])
ORG_ID = "00000000-0000-0000-0000-000000000001"

# Sentinel distinguishing "caller did not prefetch the tenants row" from "prefetched and absent"
# (None), so the login hot path never re-queries a row bootstrap already looked up.
_TENANT_UNFETCHED = object()


def sb():
    return get_supabase()


# ── App config: the master "enforce login" switch ─────────────────────────────────────
def _rbac_enabled_flag() -> bool:
    """The master enforce-login switch (shared by /auth-config and /bootstrap — ONE source).
    False if migration 015 hasn't run yet (table missing) — never raises."""
    try:
        rows = sb().schema("storeops").table("app_config").select("rbac_enabled") \
            .eq("id", 1).limit(1).execute().data or []
        return bool(rows[0]["rbac_enabled"]) if rows else False
    except Exception:
        return False


@router.get("/auth-config")
def get_auth_config():
    """PUBLIC: tells the frontend whether to enforce login. Default false (app open) so the
    deploy never locks anyone out; the admin flips it on once everyone is provisioned.
    Returns false if migration 015 hasn't run yet (table missing)."""
    return {"rbac_enabled": _rbac_enabled_flag()}


@router.put("/auth-config")
def set_auth_config(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                    x_active_org: str = Header(default="")):
    """Flip login enforcement on/off (from the Roles admin). Once ON, every user must sign in.

    storeops.app_config is a GLOBAL singleton (CHECK id=1) — this master switch is platform-wide, not
    per-tenant (per-tenant enforcement is MULTI_TENANT_ENFORCE in the middleware). So a single tenant's
    admin must NOT be able to flip it for everyone: it is gated to super-admins (or the bootstrap
    house-org admin), exactly like the other platform-level operations."""
    _require_super_admin(authorization, x_active_org)
    enabled = bool(body.get("rbac_enabled"))
    sb().schema("storeops").table("app_config").upsert(
        {"id": 1, "org_id": org_id, "rbac_enabled": enabled,
         "updated_at": datetime.now(timezone.utc).isoformat()}, on_conflict="id").execute()
    return {"rbac_enabled": enabled}


# ── Portal reports: which reports are surfaced in the employee portal + to which roles (mig 052) ──
@router.get("/portal-reports")
def get_portal_reports(org_id: str = ORG_ID):
    """Per-report portal config keyed by href: {href: {enabled, roles[], label, category}}. The
    Reports hub merges this over the report catalog; the portal/employee surfaces read it to gate
    what each role sees. Empty {} if migration 052 hasn't run."""
    try:
        rows = sb().schema("storeops").table("portal_reports").select("*").eq("org_id", org_id).execute().data or []
    except Exception:
        return {"config": {}}
    return {"config": {r["href"]: {"enabled": bool(r.get("enabled", True)), "roles": r.get("roles") or [],
                                   "label": r.get("label"), "category": r.get("category")} for r in rows if r.get("href")}}


@router.put("/portal-reports")
def set_portal_report(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                      x_active_org: str = Header(default="")):
    """Upsert one report's portal config. Body: {href, enabled, roles[], label?, category?}."""
    _require_setting(authorization, x_active_org, "security")
    href = (body.get("href") or "").strip()
    if not href:
        raise HTTPException(400, "href required")
    row = {"org_id": org_id, "href": href,
           "enabled": bool(body.get("enabled", True)),
           "roles": body.get("roles") or [],
           "label": body.get("label"), "category": body.get("category"),
           "updated_at": datetime.now(timezone.utc).isoformat()}
    try:
        sb().schema("storeops").table("portal_reports").upsert(row, on_conflict="org_id,href").execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 052 first: {e}")
    return {"ok": True, "href": href}


# ── Identity (token-verified "who am I") ───────────────────────────────────────────────
# token → (uid, expiry): POSITIVE results only, 60s TTL — mirrors tenant_middleware._cache (which
# serves only the middleware; this one serves this router + every importer, e.g. storeops'
# caller_scope, so the SAME token stops being network-re-verified several times per page load).
# Negative results are NOT cached, so a transient Supabase hiccup or an expired token never pins a
# user out for the TTL. Bounded: expired entries are evicted opportunistically on insert; if the cap
# is still exceeded the cache is cleared outright (cheap + safe — worst case one extra verification
# per live token). Do NOT cache failures here; do NOT touch tenant_middleware's own cache.
_uid_cache: dict = {}
_UID_TTL = 60.0
_UID_CACHE_MAX = 1024


def _uid_from_token(authorization: str):
    """Validate the Supabase Auth JWT (server-side) and return its auth user id, or None.
    Verified results are cached 60s per token (positive only) — /me, /my-tenants, /bootstrap and
    storeops' caller_scope all verify the SAME token within one page load; without the cache each
    call paid a full network auth.get_user round trip."""
    if not isinstance(authorization, str):
        # A route handler called IN-PROCESS (notify's report builders, module-to-module reuse)
        # binds FastAPI's `Header(default="")` SENTINEL OBJECT here instead of a string, and
        # `.lower()` on it raised AttributeError — a 500 from deep inside auth for what is simply
        # "this call has no caller" (POST /notify/send, 2026-07-17). Treat it as no token: every
        # authorization site already fails closed on a None uid. Callers should still pass the real
        # header (the notify builders now do) — this only stops the class from 500-ing.
        return None
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None
    now = time.time()
    hit = _uid_cache.get(token)
    if hit and hit[1] > now:
        return hit[0]
    try:
        resp = get_supabase_admin().auth.get_user(token)
        user = getattr(resp, "user", None) or resp
        uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    except Exception:
        return None
    if uid:
        if len(_uid_cache) >= _UID_CACHE_MAX:
            for k in [k for k, v in _uid_cache.items() if v[1] <= now]:
                _uid_cache.pop(k, None)
            if len(_uid_cache) >= _UID_CACHE_MAX:
                _uid_cache.clear()
        _uid_cache[token] = (uid, now + _UID_TTL)
    return uid


# ── Multi-tenant membership (platform-core-9) ──────────────────────────────────────────
# One Supabase login (auth_id) may hold an app_users row PER tenant it belongs to (mig 706). These
# `_memberships` (= list_memberships) and `_pick_membership` (= pick_membership) are imported from
# app.modules.core.membership at the top of this file — the single source of truth for the membership
# selection rule. They pick the membership row for the tenant the request is ACTING AS: the active
# tenant is declared by the client via the `x-active-org` header — UNTRUSTED, so it is honored ONLY
# when it names one of the login's memberships; otherwise the login's default membership is used. This
# mirrors the tenant-middleware rule exactly (single source of truth for "which tenant am I").


@router.get("/my-tenants")
def my_tenants(authorization: str = Header(default="")):
    """The tenants this ONE login belongs to (drives the post-login tenant picker + the top-bar
    switcher). Resolves purely from the token's auth_id, so it works BEFORE an active tenant is
    chosen. A single-membership login returns exactly one row ⇒ the frontend shows no picker."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    return _my_tenants_payload(sb(), uid)


def _my_tenants_payload(client, uid, rows=None, tmap=None, role_map=None):
    """Body of GET /my-tenants, shared with /bootstrap (ONE source — never duplicate this logic).
    `rows` lets a caller that already fetched the membership rows (bootstrap) share them; when None
    they are fetched here exactly as the endpoint always did. `tmap` ({org_id: tenants row}) and
    `role_map` ({(org_id, name): roles row}) likewise share bootstrap's batched fetches — when None
    each is fetched here per-row exactly as before."""
    if rows is None:
        rows = _memberships(client, uid)
    if not rows:
        return {"tenants": [], "count": 0}
    # tenant display names + per-membership role display (roles are per-org)
    orgs = [r.get("org_id") for r in rows if r.get("org_id")]
    if tmap is None:
        tmap = {}
        try:
            tens = (client.schema("storeops").table("tenants").select("org_id,name,slug")
                    .in_("org_id", orgs).execute().data) or []
            tmap = {t["org_id"]: t for t in tens}
        except Exception:
            pass
    default_org = next((r.get("org_id") for r in rows if r.get("is_default_org")), (orgs[0] if orgs else None))
    out = []
    for r in rows:
        o = r.get("org_id")
        if not o:
            continue
        t = tmap.get(o, {})
        rdisp = r.get("role")
        try:
            if r.get("role"):
                if role_map is not None:
                    rr0 = role_map.get((o, r["role"]))
                    if rr0:
                        rdisp = rr0.get("display_name") or r.get("role")
                else:
                    rr = (client.schema("storeops").table("roles").select("display_name")
                          .eq("org_id", o).eq("name", r["role"]).limit(1).execute().data) or []
                    if rr:
                        rdisp = rr[0].get("display_name") or r.get("role")
        except Exception:
            pass
        out.append({
            "org_id": o,
            "name": t.get("name") or "Tenant",
            "slug": t.get("slug"),
            "role": r.get("role"),
            "role_display": rdisp,
            "is_default": (o == default_org),
            "super_admin": bool(r.get("super_admin")),
            "is_active": bool(r.get("is_active", True)),
        })
    return {"tenants": out, "count": len(out), "default_org": default_org,
            "super_admin": any(r.get("super_admin") for r in rows)}


@router.get("/me")
def whoami(background_tasks: BackgroundTasks, authorization: str = Header(default=""),
           x_active_org: str = Header(default=""), x_2fa_token: str = Header(default="")):
    """The logged-in user's profile + resolved role permissions FOR THE ACTIVE TENANT. Token-verified
    — the frontend sends the Supabase session access token as `Authorization: Bearer <token>` and, for
    a login that belongs to >1 tenant, the chosen tenant as `x-active-org`. The active tenant is
    membership-checked (an x-active-org the login doesn't belong to falls back to the default
    membership) so a single-tenant login is entirely unaffected."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    return _me_payload(sb(), uid, x_active_org, x_2fa_token, bg=background_tasks)


def _post_login_writes(user_row_id, org_id, seed_stale):
    """Fire-and-forget per-login writes: stamp last_login, then (when the tenant's stamped
    seed_version is behind the code's SEED_VERSION) run the self-provision sync. Runs as a
    BackgroundTask AFTER the response on the /me + /bootstrap hot path — neither write changes
    anything the response itself returns (sync_tenant touches tenant_modules + seeded default
    content, never the membership/roles/tenants fields already serialized). Order (stamp first,
    sync second) and best-effort semantics match the old inline code exactly; sync_tenant stays
    idempotent and stamps seed_version only on success, so a failed seed retries next login."""
    client = sb()
    try:
        client.schema("storeops").table("app_users").update(
            {"last_login": datetime.now(timezone.utc).isoformat()}).eq("id", user_row_id).execute()
    except Exception:
        pass
    if seed_stale:
        try:
            sync_tenant(client, org_id)
        except Exception:
            pass


def _me_payload(client, uid, x_active_org="", x_2fa_token="", rows=None,
                tenant_row=_TENANT_UNFETCHED, role_map=None, bg=None):
    """Body of GET /me, shared with /bootstrap (ONE source — never duplicate this logic). `rows`
    lets a caller that already fetched the membership rows (bootstrap) share them; `tenant_row`
    (the acting org's storeops.tenants row — pass None for fetched-and-absent) and `role_map`
    ({(org_id, name): roles row}) likewise share bootstrap's batched fetches. `bg`
    (BackgroundTasks) defers the fire-and-forget post-login writes to after the response; when
    None they run inline as before. Semantics are otherwise byte-identical to calling /me with
    the same headers."""
    if rows is None:
        rows = _memberships(client, uid)
    u = _pick_membership(rows, (x_active_org or "").strip() or None)
    if not u:
        return {"provisioned": False, "user": None, "permissions": {}}
    org_id = u.get("org_id") or ORG_ID
    # ONE tenants-row fetch serves the tenant info, seed-version check and password/2FA policies
    # below (this same row was previously fetched up to 4x per request).
    t = tenant_row
    if t is _TENANT_UNFETCHED:
        try:
            t = _tenant_row(client, org_id)
        except Exception:
            t = None
    perms = {}
    if u.get("role"):
        rr_row = None
        if role_map is not None:
            rr_row = role_map.get((org_id, u["role"]))
        else:
            rr = client.schema("storeops").table("roles").select("display_name,permissions") \
                .eq("org_id", org_id).eq("name", u["role"]).limit(1).execute().data or []
            rr_row = rr[0] if rr else None
        if rr_row:
            perms = rr_row.get("permissions") or {}
            u["role_display"] = rr_row.get("display_name")
    # Post-login writes: best-effort last_login stamp + self-provision seed sync (how a NEW
    # feature auto-propagates to every existing tenant when SEED_VERSION bumps). Staleness is
    # decided off the already-fetched tenants row — mirroring needs_sync: no row (or no
    # seed_version column, pre-mig-076) => never stale.
    seed_stale = bool(t) and ("seed_version" in t) and (
        t["seed_version"] is None or t["seed_version"] < SEED_VERSION)
    if bg is not None:
        bg.add_task(_post_login_writes, u["id"], org_id, seed_stale)
    else:
        _post_login_writes(u["id"], org_id, seed_stale)
    # Tenant pay-period + onboarding-setup status (mig 085) — powers the "finish setup" banner
    # (banner only, nothing blocked) and lets the schedule/payroll derive the tenant's work-week.
    tenant = None
    try:
        if t:
            tenant = {"org_id": t.get("org_id"), "name": t.get("name"),
                      "setup_complete": bool(t.get("setup_complete")),
                      "pay_period": _pp_settings(t)}
    except Exception:
        pass
    # Tenant carriers (mig 038) — drive carrier-scoped nav gating (a Boost tenant shouldn't see Total
    # pages, and vice-versa). Empty list = no carrier chosen yet → the frontend hides nothing.
    carriers = []
    try:
        cr = (client.schema("commcalc").table("carrier").select("name,code,is_default")
              .eq("org_id", org_id).execute().data) or []
        carriers = [{"name": c.get("name"), "code": c.get("code"), "is_default": c.get("is_default")}
                    for c in cr if c.get("name")]
    except Exception:
        pass
    # Auth-hardening: the tenant password policy (client-side strength hints) + the 2FA gate for the
    # active tenant/user. Best-effort — un-run migs degrade to code defaults / 2FA off (no lockout).
    pw_policy = _load_password_policy(client, org_id, t=t)
    tw_policy = _load_twofa_policy(client, org_id, t=t)
    twofa = {"required": _twofa_required_for(tw_policy, u.get("role"), u.get("twofa_enabled")),
             "verified": bool(_sec.twofa_token_valid_for(x_2fa_token, uid, org_id, _sec.now_ts())),
             "mode": tw_policy["mode"], "user_channels": u.get("twofa_channels") or ["email"]}
    return {"provisioned": True, "user": u, "permissions": perms,
            "active": bool(u.get("is_active", True)), "tenant": tenant, "carriers": carriers,
            "password_policy": pw_policy, "twofa": twofa,
            "default_cc": tw_policy.get("default_cc", _sec.DEFAULT_COUNTRY_CODE)}


@router.get("/bootstrap")
def bootstrap(background_tasks: BackgroundTasks, authorization: str = Header(default=""),
              x_active_org: str = Header(default=""), x_2fa_token: str = Header(default="")):
    """ONE post-sign-in call that replaces the frontend's sequential login waterfall (auth-config →
    my-tenants → pending-connections → me = 4 blocking round trips before first paint). Returns
    {rbac_enabled, tenants: <the /my-tenants payload>, pending: <the /pending-connections payload>,
    me: <the full /core/me payload or null>, active_org}. Token-verified exactly like /me (self-gates
    on Authorization; allowlisted in tenant_middleware with the same rationale as /core/me, incl.
    reachability BEFORE 2FA verification so the OTP flow can start). Every payload comes from the
    SAME shared helper as its original endpoint — the old endpoints stay fully intact and there is
    exactly one copy of each membership/fallback rule.

    Picker parity: when the login belongs to >1 tenant and x-active-org does not name one of them,
    `me` is null (the frontend shows the tenant picker, exactly mirroring today's flow — it never
    called /me in that state either). A single-membership or unprovisioned login always gets `me`
    resolved — x-active-org honored-iff-member, else default membership — byte-identical to /me."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    rows = _memberships(client, uid)   # fetched ONCE; shared into both payload helpers below
    member_orgs = [r.get("org_id") for r in rows if r.get("org_id")]
    # Batched fetches shared into every payload helper (perf: the old path fetched the tenants row
    # up to 4x and the roles row twice per login). Superset .in_ queries keyed exactly afterwards;
    # on ANY failure the map stays None and each helper falls back to fetching for itself.
    tmap = None
    try:
        tens = (client.schema("storeops").table("tenants").select("*")
                .in_("org_id", member_orgs).execute().data) or []
        tmap = {tr["org_id"]: tr for tr in tens}
    except Exception:
        tmap = None
    role_map = None
    try:
        role_names = sorted({r["role"] for r in rows if r.get("role")})
        if member_orgs and role_names:
            rls = (client.schema("storeops").table("roles")
                   .select("org_id,name,display_name,permissions")
                   .in_("org_id", member_orgs).in_("name", role_names).execute().data) or []
            role_map = {(rl["org_id"], rl["name"]): rl for rl in rls}
        else:
            role_map = {}
    except Exception:
        role_map = None
    tenants = _my_tenants_payload(client, uid, rows=rows, tmap=tmap, role_map=role_map)
    pending = _pending_connections_payload(
        client, uid, email=next((r.get("email") for r in rows if r.get("email")), None))
    requested = (x_active_org or "").strip()
    if len(member_orgs) > 1 and requested not in member_orgs:
        me = None                      # >1 membership, no valid choice yet → frontend shows the picker
    else:
        acting = _pick_membership(rows, requested or None)
        trow = (tmap.get(acting.get("org_id") or ORG_ID)
                if (tmap is not None and acting) else _TENANT_UNFETCHED)
        me = _me_payload(client, uid, requested, x_2fa_token, rows=rows,
                         tenant_row=trow, role_map=role_map, bg=background_tasks)
    active_org = ((me.get("user") or {}).get("org_id")) if me else None
    return {"rbac_enabled": _rbac_enabled_flag(), "tenants": tenants, "pending": pending,
            "me": me, "active_org": active_org}


@router.post("/me/password-changed")
def password_changed(authorization: str = Header(default="")):
    """Clear the must_reset_password flag after the user sets a new password."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    sb().schema("storeops").table("app_users").update({"must_reset_password": False}) \
        .eq("auth_id", uid).execute()
    return {"ok": True}


# ── Tenants (SaaS logins) — super-admin onboarding ────────────────────────────────────
# A tenant = an org_id + metadata. Super-admins create tenants and provision each tenant's first
# admin login; that admin then manages their own company's users. ADDITIVE — single-tenant app
# is unaffected (org_id-from-session is a later phase). Backend is the guard (super_admin gate).
def _app_user_from_token(authorization: str, active_org: str = ""):
    """The caller's app_users row for the ACTIVE tenant (x-active-org). A single-membership login
    resolves to its one row regardless of active_org (today's behaviour)."""
    uid = _uid_from_token(authorization)
    if not uid:
        return None
    return _pick_membership(_memberships(sb(), uid), (active_org or "").strip() or None)


def _require_super_admin(authorization: str, active_org: str = ""):
    """Super-admin = the super_admin flag on ANY of the login's memberships (super_admin is a
    login-level bypass, not a per-tenant grant), OR (bootstrap) a house-org admin — so the very
    first operator is never locked out before the flag is seeded."""
    uid = _uid_from_token(authorization)
    rows = _memberships(sb(), uid) if uid else []
    if any(r.get("super_admin") for r in rows):
        return _pick_membership(rows, (active_org or "").strip() or None)
    u = _pick_membership(rows, (active_org or "").strip() or None)
    if u and u.get("org_id") == ORG_ID and u.get("role") == "admin":
        return u
    raise HTTPException(403, "super-admin only")


def _require_setting(authorization: str, active_org: str, area: str):
    """Server-side gate for a permission-controlled ADMIN operation. Resolves the caller from the
    verified JWT (never from a client-set body/header) and requires edit rights on `area` via the
    existing `_can_edit_setting` precedence (super_admin → explicit settings[area] grant/deny →
    full-scope admin). Returns the caller dict on success.

    Raises 401 when the request carries no valid token and 403 when the caller lacks the right. This
    is the SAME gate the account-security endpoints already use (e.g. POST /users/set-password gates on
    area 'security'); applying it to the role/user write paths closes the privilege-escalation holes
    where those endpoints self-gated on nothing but the UI hiding the control. `_can_edit_setting` is
    defined later in this module — resolved at call time, which is fine (this only runs per request)."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    caller = _resolve_caller(sb(), uid, active_org)
    if not _can_edit_setting(caller, area):
        raise HTTPException(403, f"not authorized to edit {area}")
    return caller


def _mods(**on):
    # ONE key universe (platform-core-3): canonical entitlement modules (MODULE_CATALOG) + role-gate
    # keys (admin). A module key present with False is equivalent to ABSENT for every RBAC consumer
    # (canSeeItem/canAccessPath do `!modules[m]`), so widening the base to the full catalog is
    # behavior-neutral — it only removes the drift between this seed list and the catalog.
    base = {k: False for k in (*MODULE_CATALOG.keys(), *ROLE_GATE_KEYS)}
    base.update(on)
    return base


# the role set seeded into every new tenant (mirror of migration 015 + helpdesk); the tenant admin
# edits them afterward on their own Roles & Access.
# `closing` (Daily Closing) is a store-operations feature: every tier that already gets `storeops`
# (admin / market / store manager) also gets `closing` so a NEW tenant's managers can run closings out
# of the box — the seed list stays in lockstep with MODULE_CATALOG. FORWARD-ONLY: this seeds roles at
# tenant CREATION; it never rewrites an existing tenant's role rows, so the house/Boost org is untouched
# (byte-identical). An existing tenant grants/revokes any module per role on /admin/roles.
_BASE_ROLES = [
    ("admin", "Admin", {"modules": _mods(commissions=True, targets=True, asset=True, vip=True, storeops=True, closing=True, notify=True, helpdesk=True, hr=True, ai_assistant=True, admin=True), "scope": "all", "home": "/commcalc"}),
    ("market_manager", "Market Manager", {"modules": _mods(commissions=True, targets=True, asset=True, vip=True, storeops=True, closing=True, notify=True, helpdesk=True, hr=True, ai_assistant=True), "scope": "market", "home": "/commcalc/targets"}),
    ("store_manager", "Store Manager", {"modules": _mods(commissions=True, targets=True, asset=True, storeops=True, closing=True, helpdesk=True, ai_assistant=True), "scope": "store", "home": "/commcalc/targets"}),
    ("sales_rep", "Sales Rep", {"modules": _mods(targets=True, helpdesk=True), "scope": "self", "home": "/commcalc/targets/my"}),
]


@router.get("/tenants")
def list_tenants(authorization: str = Header(default="")):
    _require_super_admin(authorization)
    client = sb()
    tens = client.schema("storeops").table("tenants").select("*").order("created_at").execute().data or []
    users = client.schema("storeops").table("app_users").select("org_id,auth_id").execute().data or []
    cnt: dict = {}
    for u in users:
        o = u.get("org_id")
        c = cnt.setdefault(o, {"users": 0, "logins": 0})
        c["users"] += 1
        if u.get("auth_id"):
            c["logins"] += 1
    for t in tens:
        t.update(cnt.get(t["org_id"], {"users": 0, "logins": 0}))
    return {"tenants": tens}


def _provision_tenant(client, name, admin_email, admin_name=None, password=None, slug=None, must_reset=True):
    """Create a tenant (org_id) + seed base roles + module entitlements + provision its first admin
    login. Shared by super-admin create-tenant AND self-serve signup. Returns the temp password only
    when one was auto-generated (super-admin flow), not when the caller chose it (signup)."""
    new_org = str(uuid.uuid4())
    slug = (slug or re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-"))[:60]
    client.schema("storeops").table("tenants").insert({"org_id": new_org, "name": name, "slug": slug}).execute()
    client.schema("storeops").table("roles").insert(
        [{"org_id": new_org, "name": n, "display_name": d, "permissions": p} for (n, d, p) in _BASE_ROLES]).execute()
    # Entitlement + tenant-safe default content in one shot: enables modules per the (all-access
    # default) plan and seeds HR onboarding / store-visit checklist / default company / etc.
    # (carrier-neutral — the tenant adds its own carrier on Mapping → Carriers).
    try:
        sync_tenant(client, new_org)
    except Exception:
        pass  # entitlement engine (mig 053/076) may be absent in some envs — non-fatal
    pw = password or _gen_temp_pw()
    auth_id, created, err = _create_or_link_auth(get_supabase_admin(), admin_email, pw)
    client.schema("storeops").table("app_users").insert({
        "org_id": new_org, "auth_id": auth_id, "email": admin_email,
        "full_name": admin_name or f"{name} Admin", "role": "admin",
        "is_active": True, "must_reset_password": must_reset, "super_admin": False,
    }).execute()
    return {"org_id": new_org, "name": name, "admin_email": admin_email,
            "temp_password": (None if password else pw), "auth_created": created, "auth_error": err}


@router.post("/tenants")
def create_tenant(body: dict, authorization: str = Header(default="")):
    """Super-admin: create a tenant + provision its first admin login (returns a temp password)."""
    _require_super_admin(authorization)
    name = (body.get("name") or "").strip()
    admin_email = (body.get("admin_email") or "").strip().lower()
    if not name or not admin_email:
        raise HTTPException(400, "name and admin_email required")
    return _provision_tenant(sb(), name, admin_email, body.get("admin_name"),
                             password=body.get("temp_password"), slug=body.get("slug"), must_reset=True)


# ─────────────────────────────────────────────
# PLATFORM SUPER-ADMINS — logins that bypass tenant isolation (cross-tenant operators).
# Audit + manage here instead of one-off SQL (mig 055 blanket-elevated house admins → drift).
# ─────────────────────────────────────────────

@router.get("/super-admins")
def list_super_admins(authorization: str = Header(default="")):
    """Super-admin: every login holding platform-wide (cross-tenant) access. These bypass tenant
    scoping (tenant_middleware honours super_admin), so this is the audit surface for who holds the keys."""
    _require_super_admin(authorization)
    rows = (sb().schema("storeops").table("app_users")
            .select("id,email,full_name,role,org_id,is_active,last_login")
            .eq("super_admin", True).order("email").execute().data) or []
    return {"super_admins": rows}


@router.post("/super-admins")
def create_super_admin(body: dict, authorization: str = Header(default="")):
    """Super-admin: mint OR elevate a PLATFORM super-admin. A brand-new email gets a Supabase Auth
    login created (house org = the platform home; super_admin bypasses tenant scoping regardless of
    org_id) and a temp password returned. An EXISTING login is simply flagged — its password is left
    untouched. Idempotent."""
    _require_super_admin(authorization)
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    client = sb()
    existing = (client.schema("storeops").table("app_users").select("*")
                .eq("org_id", ORG_ID).eq("email", email).limit(1).execute().data) or []
    # Existing, already-linked login → just elevate; never touch their credential.
    if existing and existing[0].get("auth_id"):
        client.schema("storeops").table("app_users").update(
            {"super_admin": True, "role": "admin", "is_active": True}
        ).eq("id", existing[0]["id"]).execute()
        return {"email": email, "elevated": True, "created": False, "temp_password": None}
    # New login (or an orphan row without an auth account) → create/link auth + stamp super_admin.
    chose_pw = bool((body.get("temp_password") or "").strip())
    pw = (body.get("temp_password") or "").strip() or _gen_temp_pw()
    auth_id, created, err = _create_or_link_auth(get_supabase_admin(), email, pw)
    if not auth_id:
        raise HTTPException(500, f"could not create login: {err}")
    fields = {"org_id": ORG_ID, "auth_id": auth_id, "email": email,
              "full_name": (body.get("full_name") or "").strip() or None,
              "role": "admin", "is_active": True, "super_admin": True,
              "must_reset_password": not chose_pw}
    if existing:
        client.schema("storeops").table("app_users").update(fields).eq("id", existing[0]["id"]).execute()
    else:
        client.schema("storeops").table("app_users").insert(fields).execute()
    return {"email": email, "elevated": bool(existing), "created": created,
            "temp_password": (None if chose_pw else pw)}


@router.delete("/super-admins")
def revoke_super_admin(email: str = "", authorization: str = Header(default="")):
    """Super-admin: strip platform access from a login (demote to a normal tenant-scoped user). Does
    NOT delete the login. Refuses to remove the LAST super-admin so the platform can't be locked out."""
    _require_super_admin(authorization)
    email = (email or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    client = sb()
    supers = (client.schema("storeops").table("app_users").select("id,email")
              .eq("super_admin", True).execute().data) or []
    if len(supers) <= 1:
        raise HTTPException(400, "cannot remove the last platform super-admin")
    if not any((s.get("email") or "").lower() == email for s in supers):
        raise HTTPException(404, "no super-admin with that email")
    (client.schema("storeops").table("app_users").update({"super_admin": False})
     .eq("super_admin", True).eq("email", email).execute())
    return {"email": email, "revoked": True}


@router.get("/modules")
def list_modules():
    """PUBLIC: the canonical module registry (module_key → label). Single source of truth is
    core.module_catalog (mig 700), with an in-code fallback so an unrun migration is a no-op.
    Drives the billing plan editor's per-module picker and the tenant entitlement view."""
    cat = load_module_catalog(sb())
    return {"modules": [{"key": k, "label": v} for k, v in cat.items()]}


@router.post("/tenants/sync")
def sync_tenants_endpoint(authorization: str = Header(default=""), x_notify_secret: str = Header(default="")):
    """Reconcile EVERY tenant — module entitlement (all-access default) + tenant-safe default
    content — bringing tenants created before a feature shipped up to date. Auth: super-admin,
    OR the NOTIFY_RUN_SECRET header (so a post-deploy / cron backfill can run without a UI token)."""
    if not (settings.NOTIFY_RUN_SECRET and x_notify_secret == settings.NOTIFY_RUN_SECRET):
        _require_super_admin(authorization)
    return sync_all_tenants(sb())


@router.post("/tenants/{org_id}/sync")
def sync_one_tenant_endpoint(org_id: str, authorization: str = Header(default="")):
    """Super-admin: reconcile a SINGLE tenant's entitlement + default content."""
    _require_super_admin(authorization)
    return sync_tenant(sb(), org_id)


def _signups_open() -> bool:
    return os.environ.get("SIGNUPS_OPEN", "").lower() in ("1", "true", "yes")


@router.get("/signup-status")
def signup_status():
    """PUBLIC: whether self-serve signup is open (env SIGNUPS_OPEN). The /signup page reads this."""
    return {"open": _signups_open()}


@router.post("/signup")
def signup(body: dict):
    """PUBLIC self-serve signup — GATED on env SIGNUPS_OPEN (default OFF). Creates a new company + its
    admin login with the chosen password. ⚠️ v1 auto-confirms the email — add real email verification
    + rate-limit/captcha before opening this to the public internet."""
    if not _signups_open():
        raise HTTPException(403, "signups are closed")
    name = (body.get("name") or "").strip()
    admin_email = (body.get("admin_email") or "").strip().lower()
    password = body.get("password") or ""
    if not name or not admin_email:
        raise HTTPException(400, "company name and email are required")
    if "@" not in admin_email or "." not in admin_email.split("@")[-1]:
        raise HTTPException(400, "a valid email is required")
    # New company has no tenant row yet → enforce the owner DEFAULT policy on the chosen password.
    perr = _sec.password_errors(_sec.DEFAULT_PASSWORD_POLICY, password)
    if perr:
        raise HTTPException(400, " ".join(perr))
    client = sb()
    if client.schema("storeops").table("app_users").select("id").eq("email", admin_email).limit(1).execute().data:
        raise HTTPException(409, "an account with this email already exists")
    res = _provision_tenant(client, name, admin_email, body.get("admin_name"), password=password, must_reset=False)
    return {"org_id": res["org_id"], "name": name, "admin_email": admin_email,
            "message": "Company created — sign in with your email and password."}


@router.patch("/tenants/{org_id}")
def update_tenant(org_id: str, body: dict, authorization: str = Header(default="")):
    _require_super_admin(authorization)
    upd = {}
    if "name" in body:
        upd["name"] = body["name"]
    if "is_active" in body:
        upd["is_active"] = bool(body["is_active"])
    if not upd:
        raise HTTPException(400, "nothing to update")
    sb().schema("storeops").table("tenants").update(upd).eq("org_id", org_id).execute()
    return {"ok": True}


# ── Per-tenant pay period / work-week (migration 085) ────────────────────────────────────────
# DOW convention throughout: 0=Mon .. 6=Sun (matches Python date.weekday()).
_PP_FIELDS = ("work_week_start_dow", "pay_period_type", "payday_dow", "payday_weeks_after",
              "biweekly_anchor", "timezone")
_PP_DEFAULTS = {"work_week_start_dow": 0, "pay_period_type": "weekly", "payday_dow": 4,
                "payday_weeks_after": 1, "biweekly_anchor": None, "timezone": None}


def _pp_settings(t: dict) -> dict:
    """Normalize a tenants row into pay-period settings with safe defaults (Monday week today)."""
    def _int(v, d):
        try:
            return int(v)
        except (TypeError, ValueError):
            return d
    return {
        "work_week_start_dow": _int(t.get("work_week_start_dow"), 0) % 7,
        "pay_period_type": (t.get("pay_period_type") or "weekly"),
        "payday_dow": _int(t.get("payday_dow"), 4) % 7,
        "payday_weeks_after": max(1, _int(t.get("payday_weeks_after"), 1)),
        "biweekly_anchor": t.get("biweekly_anchor"),
        "timezone": t.get("timezone"),
        "setup_complete": bool(t.get("setup_complete")),
    }


def pay_period_for(s: dict, ref):
    """The pay period CONTAINING date `ref` (a datetime.date): {start, end, payday} as ISO strings.
    period = length days starting on the most recent work_week_start_dow on/before ref; payday =
    the first payday_dow on/after the period end, advanced by (payday_weeks_after-1) weeks."""
    from datetime import date as _d
    length = 14 if (s.get("pay_period_type") == "biweekly") else 7
    delta = (ref.weekday() - s["work_week_start_dow"]) % 7
    start = ref - timedelta(days=delta)
    if length == 14 and s.get("biweekly_anchor"):
        try:
            a = _d.fromisoformat(str(s["biweekly_anchor"])[:10])
            off = (start - a).days % 14
            if off:
                start = start - timedelta(days=off)
        except Exception:
            pass
    end = start + timedelta(days=length - 1)
    pd_delta = (s["payday_dow"] - end.weekday()) % 7
    payday = end + timedelta(days=pd_delta) + timedelta(weeks=max(0, s["payday_weeks_after"] - 1))
    return {"start": start.isoformat(), "end": end.isoformat(), "payday": payday.isoformat()}


def _next_periods(s: dict, n: int = 4):
    """The current + next n-1 pay periods, for the settings UI to show a worked example."""
    from datetime import date as _d
    today = _d.fromisoformat(datetime.now(timezone.utc).astimezone().date().isoformat())
    out, cur = [], pay_period_for(s, today)
    out.append(cur)
    length = 14 if (s.get("pay_period_type") == "biweekly") else 7
    for _ in range(max(0, n - 1)):
        nxt_start = _d.fromisoformat(cur["end"]) + timedelta(days=1)
        cur = pay_period_for(s, nxt_start)
        out.append(cur)
    return out


def _tenant_row(client, org_id):
    r = (client.schema("storeops").table("tenants").select("*").eq("org_id", org_id)
         .limit(1).execute().data) or []
    return r[0] if r else None


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# AUTH HARDENING (2026-07-17) — password policy · self-serve reset · admin-set passwords · OTP · 2FA.
# Every password-setting path routes through validate_password(); temp passwords come from the
# owner-default-satisfying generator; OTP lifecycle uses the PURE decisions in auth_security.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _load_password_policy(client, org_id, t=_TENANT_UNFETCHED):
    """The tenant's effective password policy (override merged over owner defaults, all bounds clamped).
    Best-effort: any read error / un-run mig 709 → the code defaults (so enforcement is never lost).
    `t` lets the login hot path pass its already-fetched tenants row (None = known absent)."""
    raw = None
    try:
        if t is _TENANT_UNFETCHED:
            t = _tenant_row(client, org_id)
        raw = (t or {}).get("password_policy")
    except Exception:
        raw = None
    return _sec.normalize_policy(raw)


def validate_password(client, org_id, pw):
    """Enforce the tenant policy on ONE candidate password. Raises HTTPException(400) with a helpful,
    combined reason (this is used on password-SET surfaces where a helpful message is correct — the
    caller already proved control of the account). The HARD 128-cap is enforced first inside
    password_errors regardless of tenant config."""
    errs = _sec.password_errors(_load_password_policy(client, org_id), pw or "")
    if errs:
        raise HTTPException(400, " ".join(errs))


def _gen_temp_pw(client=None, org_id=None):
    """A random temp password guaranteed to satisfy the owner DEFAULT policy (>=8, all four classes),
    and the tenant policy too when org_id is given. Replaces the old 'Mp'+token_urlsafe generator that
    could omit a digit/upper/special."""
    policy = _load_password_policy(client, org_id) if (client is not None and org_id) else None
    return _sec.gen_temp_password(policy)


# ── OTP store (best-effort; degrades to 503-style when mig 710 is un-run) ─────────────────────────
class OtpUnavailable(Exception):
    """core.auth_otp is unreachable (mig 710 un-run). Surfaced as a generic 503, never a stack trace."""


def _masked_500(client, org_id, source, exc):
    """Log the real internal error to core.failure_log under a short reference id and return a GENERIC
    HTTPException(500) carrying only that id (directive item 5b — no SQL / provider / migration string
    ever reaches the client). Caller does: `raise _masked_500(...)`."""
    ref = secrets.token_hex(4)
    try:
        client.schema("core").table("failure_log").insert({
            "org_id": org_id or ORG_ID, "category": "system_error", "severity": "error",
            "source": str(source)[:200], "message": f"Auth error [{ref}] at {source}"[:1000],
            "detail": {"ref": ref, "error": str(exc)[:1200]},
            "remediation": "Search core.failure_log for this reference id to see the internal detail.",
        }).execute()
    except Exception:
        pass
    return HTTPException(500, f"A system error occurred. Reference: {ref}")


OTP_TTL_MIN = 10
OTP_MAX_ATTEMPTS = 5
OTP_RATE_MAX = 5          # max codes issued per email+purpose per window
OTP_RATE_WINDOW_MIN = 15


def _client_ip(request):
    try:
        xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        return xff or (request.client.host if request and request.client else None)
    except Exception:
        return None


def _issue_otp(client, *, email, purpose, channel, org_id=None, auth_id=None, dest=None, ip=None):
    """Create + persist a hashed OTP for (email, purpose). Returns the plaintext code (to send) or
    raises OtpUnavailable (mig un-run) / HTTPException(429) (rate-limited). PURE decisions delegated to
    auth_security so they're unit-proven."""
    email = (email or "").strip().lower()
    now = datetime.now(timezone.utc)
    try:
        since = (now - timedelta(minutes=OTP_RATE_WINDOW_MIN)).isoformat()
        recent = (client.schema("core").table("auth_otp").select("id")
                  .eq("email", email).eq("purpose", purpose).gte("created_at", since)
                  .execute().data) or []
    except Exception:
        raise OtpUnavailable()
    if _sec.otp_rate_limited(len(recent), OTP_RATE_MAX):
        raise HTTPException(429, "Too many codes requested — please wait a few minutes and try again.")
    code = _sec.gen_otp()
    row = {
        "org_id": org_id, "email": email, "auth_id": auth_id, "purpose": purpose,
        "channel": channel, "code_hash": _sec.hash_otp(code, email), "dest": dest,
        "attempts": 0, "max_attempts": OTP_MAX_ATTEMPTS,
        "expires_at": (now + timedelta(minutes=OTP_TTL_MIN)).isoformat(),
        "request_ip": ip,
    }
    try:
        client.schema("core").table("auth_otp").insert(row).execute()
    except Exception:
        raise OtpUnavailable()
    return code


def _verify_otp(client, *, email, purpose, code):
    """Verify a submitted code against the newest live OTP for (email, purpose). Returns (ok, reason).
    Increments attempts on a mismatch; consumes the row on success. Best-effort; a store error →
    (False,'unavailable')."""
    email = (email or "").strip().lower()
    now_ts = _sec.now_ts()
    try:
        rows = (client.schema("core").table("auth_otp").select("*")
                .eq("email", email).eq("purpose", purpose).is_("consumed_at", "null")
                .order("created_at", desc=True).limit(1).execute().data) or []
    except Exception:
        return (False, "unavailable")
    if not rows:
        return (False, "missing")
    row = rows[0]
    ok, reason = _sec.otp_verify_decision(row, code, email, now_ts, max_attempts=OTP_MAX_ATTEMPTS)
    try:
        if ok:
            client.schema("core").table("auth_otp").update(
                {"consumed_at": datetime.now(timezone.utc).isoformat()}).eq("id", row["id"]).execute()
        elif reason == "mismatch":
            client.schema("core").table("auth_otp").update(
                {"attempts": int(row.get("attempts") or 0) + 1}).eq("id", row["id"]).execute()
    except Exception:
        pass
    return (ok, reason)


# ── Per-setting edit permissions ─────────────────────────────────────────────────────────────
# Every editable settings area is registered here so a tenant admin can grant/deny editing of each
# one PER ROLE (permissions.settings[<key>] = true/false), on top of the role's data scope. Add a
# row here + gate the area's write endpoint with _can_edit_setting(caller, key) to make a new setting
# permission-controlled.
SETTING_AREAS = [
    {"key": "pay_period",        "label": "Pay Period & Work-Week"},
    {"key": "carriers",          "label": "Carrier Selection"},
    {"key": "commission_rates",  "label": "Boost Commission Rates"},
    {"key": "commission_plans",  "label": "Commission Plans & Payout Schedules"},
    {"key": "commission_promote", "label": "Expected → Earned promote (multi-month commission)"},
    {"key": "targets",           "label": "Target Settings"},
    {"key": "closing",           "label": "Daily Closing / Tender Fields"},
    {"key": "kpi",               "label": "KPI Metrics"},
    {"key": "performance_review", "label": "Performance Review / Productivity config"},
    {"key": "notify_policy",     "label": "Notify (report-delivery policy · download-link expiry)"},
    {"key": "classification",    "label": "Sales Classification settings (accessory / box / bill-payment / set-up-fee / contract-type map)"},
    {"key": "agency",            "label": "Agency (Master/Sub-Agent) — transfer confirm & config"},
    {"key": "asset_purchase_orders", "label": "Purchase Orders (vendors · aging threshold)"},
    {"key": "expenses",          "label": "Store Expenses"},
    {"key": "labels",            "label": "Display Labels"},
    {"key": "menu",              "label": "Menu Layout"},
    {"key": "failures",          "label": "Failure Logs (clock-in sensitivity, logged categories)"},
    {"key": "security",          "label": "Security (password policy · 2FA · admin-set passwords)"},
    {"key": "support_config",    "label": "Tech Support (SLA policy · canned responses · help docs)"},
    {"key": "import_health",     "label": "Import Health (feed schedules · expected cadence · snooze)"},
    # Registered 2026-08-05 (owner hit "you don't have permission to save" on Reviews Setup).
    # storeops._require_google_reviews_admin has always gated on this key; until it was listed here it
    # could not be granted per-role in the Roles UI, so only a super-admin could save the API key.
    {"key": "google_reviews",    "label": "Google Reviews (API key · rating targets · store place matching · sweep schedule)"},
]


def _resolve_caller(client, uid, active_org=None):
    """Resolve the signed-in user to {org_id, role, super_admin, perms} FOR THE ACTIVE TENANT.
    `active_org` is the (untrusted) x-active-org header — it selects which membership row when the
    login belongs to >1 tenant, and is honored only if it names one of the memberships. A
    single-membership login resolves to its one row regardless (today's behaviour). perms is the
    role's permissions JSONB (scope / modules / settings / ...). None if the login has no tenant."""
    u = _pick_membership(_memberships(client, uid), (active_org or "").strip() or None)
    if not u:
        return None
    org_id = u.get("org_id") or ORG_ID
    perms = {}
    if u.get("role"):
        rr = (client.schema("storeops").table("roles").select("permissions")
              .eq("org_id", org_id).eq("name", u["role"]).limit(1).execute().data) or []
        if rr:
            perms = rr[0].get("permissions") or {}
    return {"org_id": org_id, "role": u.get("role"), "super_admin": bool(u.get("super_admin")), "perms": perms}


def _can_edit_setting(caller, area):
    """True if this caller may EDIT the given settings area. Precedence:
      1. super_admin              -> always yes.
      2. explicit role grant/deny -> permissions.settings[area] (true/false) wins, even for an admin
         (so an owner can DISABLE a specific setting for a manager, or GRANT one setting to a manager).
      3. default                  -> a full-scope admin (scope='all' or the 'admin' role) edits every
         setting; anyone else cannot."""
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    s = perms.get("settings") or {}
    if area in s:
        return bool(s[area])
    return (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin")


@router.get("/setting-areas")
def list_setting_areas(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The registry of permission-controlled settings areas (drives the Roles UI toggles). Also returns
    which areas the CALLER can edit, so pages can gate their own edit affordances if they prefer."""
    uid = _uid_from_token(authorization)
    caller = _resolve_caller(sb(), uid, x_active_org) if uid else None
    return {"areas": SETTING_AREAS,
            "can_edit": {a["key"]: _can_edit_setting(caller, a["key"]) for a in SETTING_AREAS}}


# ── Failure Logs ─────────────────────────────────────────────────────────────────────────────
# A system log of failures the app hits (e.g. a valid rep rejected by kiosk face-match) WITH a
# how-to-fix note, so admins can diagnose recurring issues. Admin-only by default; grant the /failures
# page to any role to share it (RBAC). Writes are best-effort and never raise to the caller.
FAILURE_TYPES = {
    "face_mismatch": {
        "label": "Clock-in face didn't match",
        "severity": "warning",
        "remediation": ("The rep's enrolled face may be out of date (new glasses/beard, poor lighting, or a "
                        "low-quality enrollment). Fixes: (1) have the rep tap 'Re-register my face' on the "
                        "kiosk in good, even light; (2) if it's happening to many reps, raise 'Clock-in Face "
                        "Sensitivity' on the Failure Logs page toward 0.65 (looser); (3) a manager can approve "
                        "the punch right away via the kiosk manager override."),
    },
    "clock_in_location": {
        "label": "Clock-in blocked by location/schedule",
        "severity": "info",
        "remediation": ("The rep isn't at their home / scheduled / floater store. Add the shift, mark them a "
                        "floater for that store, or a manager can approve the punch via override."),
    },
    "upload_rejected": {
        "label": "Data upload rejected",
        "severity": "error",
        "remediation": ("A file failed ingest (wrong layout / missing columns / price-guard). Confirm the file "
                        "has the required columns and re-upload; for the daily sales feed the report must carry "
                        "Ext Price + GP."),
    },
    "sweep_error": {
        "label": "Email/portal import error",
        "severity": "error",
        "remediation": ("An automated import run failed. Check /commcalc/email-imports last_status + Test "
                        "connection; verify the mailbox credentials and the filename patterns."),
    },
    # Raised by core.run_for_tenant (the shared background-job guard):
    "tenant_guard": {
        "label": "Background job refused — bad/inactive tenant",
        "severity": "error",
        "remediation": ("A background job fired for an org_id that has no tenant row, or a deactivated one. "
                        "Confirm the connector / subscription / plan is filed under a REAL, active tenant at "
                        "/admin/tenants (this is the tenant-misfiling guard) — or remove the stale row."),
    },
    "money_write_refused": {
        "label": "Org-wide money write blocked (anomaly guard)",
        "severity": "error",
        "remediation": ("A background job tried to replace a whole tenant's money rows with an anomalous total "
                        "(all-$0, or a near-total wipe of an existing balance) — the 2026-07-13 $0-incident "
                        "shape. The tenant's data was LEFT AS-IS. A $0 result is almost always missing input "
                        "(no plan assignment / empty source file), not a real zero — fix the input then re-run. "
                        "If the write is legitimately zero, adjust storeops.tenants.money_guard_config."),
    },
    "other": {"label": "Other", "severity": "warning",
              "remediation": "Review the detail and resolve manually."},
}

# ── Plain-English failure registry (config-as-data, mig 716) ──────────────────────────────────
# Owner directive 2026-07-23: error codes mean nothing to an admin. Every failure KIND emitted in the
# codebase gets a layman "what this means" + "how to fix it" + "escalate when" + a code-area hint. This
# in-code dict is the FALLBACK; the EDITABLE source of truth is core.failure_kind_doc (seeded IDENTICALLY
# by mig 716 — keep the text in sync — and edited from the support docs editor). An unknown/unseeded kind
# degrades to a graceful "Unrecognized error — escalate to tech support" (owner item 3). Every kind here
# corresponds to a real failure_log.category emitted somewhere: face_mismatch/clock_in_location (storeops
# kiosk), upload_rejected (commcalc parsers), sweep_error/tenant_guard/money_write_refused (run_for_tenant),
# system_error (main.py hardening + core _masked_500), asset_upload_degraded_mode (asset upload), other.
FAILURE_KIND_META = {
    "face_mismatch": {
        "label": "Clock-in face did not match", "module": "storeops", "severity": "warning",
        "layman_meaning": ("A rep tried to clock in at the kiosk, but the live camera face did not match "
                           "the face saved on their profile closely enough, so the punch was refused."),
        "layman_fix": ("Ask the rep to tap \"Re-register my face\" at the kiosk in good, even light. If it "
                       "keeps happening to many reps, raise the Clock-in Face Sensitivity on the Failure "
                       "Logs page toward 0.65 (looser). A manager can approve the punch right away with a "
                       "kiosk override."),
        "escalate_when": "The same rep keeps getting rejected right after a fresh re-registration in good lighting.",
        "code_hint": "frontend portal kiosk clock-in + storeops face enrollment; threshold = storeops.tenants.face_match_threshold",
    },
    "clock_in_location": {
        "label": "Clock-in blocked by location or schedule", "module": "storeops", "severity": "info",
        "layman_meaning": ("A rep tried to clock in at a store that is not their home store, is not on their "
                           "schedule, and where they are not marked a floater, so the system blocked it."),
        "layman_fix": ("Add the shift for that store, mark the rep a floater for it, or have a manager "
                       "approve the punch with an override."),
        "escalate_when": "The rep is correctly scheduled or a floater for that store but is still blocked.",
        "code_hint": "storeops time-clock home/scheduled/floater gate + manager override",
    },
    "upload_rejected": {
        "label": "Data upload rejected", "module": "commissions", "severity": "error",
        "layman_meaning": ("A file that was uploaded could not be read because it had the wrong layout or "
                           "was missing required columns, so nothing was imported."),
        "layman_fix": ("Confirm the file has all required columns and re-upload. For the daily sales feed "
                       "the report must include Ext Price and GP; for commissions use the full 78-column "
                       "Sales Transaction Details export."),
        "escalate_when": "The file clearly has the required columns but is still rejected.",
        "code_hint": "commcalc upload parsers (sales / commissions); daily feed must carry Ext Price + GP",
    },
    "sweep_error": {
        "label": "Automated import or job failed", "module": "admin", "severity": "error",
        "layman_meaning": ("A scheduled background job (an email or portal import, or a nightly sweep) hit "
                           "an error and could not finish. Existing data was left as-is."),
        "layman_fix": ("Check the connection at Data Imports (last status + Test connection) and confirm the "
                       "mailbox or portal credentials and the file-name patterns. The sweep retries on its "
                       "next run once the cause is fixed."),
        "escalate_when": "Credentials and settings are confirmed correct but the job keeps failing.",
        "code_hint": "core.run_for_tenant guarded jobs; commcalc email-imports connectors",
    },
    "tenant_guard": {
        "label": "Background job refused — bad or inactive company", "module": "admin", "severity": "error",
        "layman_meaning": ("A background job fired for a company (tenant) that has no record, or one that is "
                           "switched off, so it was refused to avoid writing data to the wrong place."),
        "layman_fix": ("Make sure the connector, subscription, or plan is filed under a real, active company "
                       "at Companies (Tenants). Reactivate the company if it was switched off by mistake, or "
                       "remove the stale setup."),
        "escalate_when": "The company exists and is active but its jobs are still refused.",
        "code_hint": "core.run_for_tenant tenant-misfiling guard; storeops.tenants.is_active",
    },
    "money_write_refused": {
        "label": "Money update blocked (safety guard)", "module": "admin", "severity": "error",
        "layman_meaning": ("A background job tried to replace a whole company worth of money figures with "
                           "numbers that looked wrong (all zero, or wiping out an existing balance), so the "
                           "safety guard blocked it and left the data unchanged."),
        "layman_fix": ("A zero result is almost always missing input (no plan assigned, or an empty source "
                       "file), not a real zero — fix the input, then re-run. If the zero is genuinely "
                       "correct, adjust the money guard for that company."),
        "escalate_when": "The input is confirmed correct and the write is legitimately zero but keeps getting blocked.",
        "code_hint": "core.run_for_tenant money guard; storeops.tenants.money_guard_config",
    },
    "system_error": {
        "label": "Unexpected system error", "module": "admin", "severity": "error",
        "layman_meaning": ("Something in the app crashed unexpectedly. The user saw a generic message with a "
                           "reference code; the full technical detail is saved here under that code."),
        "layman_fix": ("Note the reference code shown to the user and open the matching entry here to read "
                       "the detail. This usually needs a developer or tech support to fix the underlying "
                       "cause."),
        "escalate_when": "Always escalate a repeating system error to tech support, with the reference code.",
        "code_hint": "app.main HardeningMiddleware + core _masked_500; search failure_log detail by ref",
    },
    "asset_upload_degraded_mode": {
        "label": "Asset upload used the older (non-atomic) path", "module": "asset", "severity": "warning",
        "layman_meaning": ("An asset ledger upload worked, but used an older import method because a database "
                           "upgrade has not been applied. If an upload were interrupted midway it could leave "
                           "a partial ledger."),
        "layman_fix": ("Run migration 300 (asset ledger staging-swap) in the Supabase SQL editor to enable "
                       "the safer atomic upload. Until then, uploads still work but are not interruption-safe."),
        "escalate_when": "Migration 300 has been run but this warning still appears on every upload.",
        "code_hint": "asset/router _stage_and_swap_ledger; migration 300_asset_ledger_staging_swap.sql",
    },
    "other": {
        "label": "Other", "module": "admin", "severity": "warning",
        "layman_meaning": "A failure that does not fit a known category. The details describe what happened.",
        "layman_fix": "Review the detail on the entry and resolve it manually. If it is unclear, escalate to tech support.",
        "escalate_when": "The cause is unclear from the detail.",
        "code_hint": "generic fallback category",
    },
}

_KIND_DOC_FIELDS = ("label", "module", "severity", "layman_meaning", "layman_fix", "escalate_when", "code_hint")


def _kind_fallback(kind):
    """The plain-English meta for `kind` from the in-code registry, or a graceful UNRECOGNISED fallback
    (owner item 3: unknown code → "escalate to tech support"). Pure."""
    m = FAILURE_KIND_META.get(kind)
    if m:
        return {"kind": kind, "known": True, "source": "code", **m}
    return {
        "kind": kind, "known": False, "source": "fallback",
        "label": (str(kind or "unknown").replace("_", " ").strip().title() or "Unrecognized error"),
        "module": "admin", "severity": "warning",
        "layman_meaning": ("This error code is not in the plain-English registry yet, so there is no guided "
                           "explanation for it."),
        "layman_fix": ("Escalate to tech support — they can add a plain-English entry for this code and advise "
                       "a fix."),
        "escalate_when": "Right away — this is an unrecognized error.",
        "code_hint": "",
    }


def _merge_kind_docs(db_rows):
    """Merge the EDITABLE DB registry (core.failure_kind_doc, HOUSE rows) OVER the in-code fallback. Returns
    {kind: meta} for every kind known to EITHER source (a DB row wins field-by-field where non-null). Pure —
    unit-proven in the harness."""
    out = {}
    for k, m in FAILURE_KIND_META.items():
        out[k] = {"kind": k, "known": True, "source": "code", **m}
    for r in (db_rows or []):
        k = (r.get("kind") or "").strip()
        if not k:
            continue
        base = out.get(k) or {"kind": k, "known": True}
        overlay = {f: r[f] for f in _KIND_DOC_FIELDS if r.get(f) is not None}
        out[k] = {**base, **overlay, "kind": k, "source": "db", "known": True}
    return out


def _build_failure_groups(rows, kind_meta):
    """PURE. Group failure rows by KIND (the natural 'similar-nature' key; the module is a property of the
    kind). Each group carries count / unreviewed_count / reviewed_count / latest_at / max severity / sample
    ids / affected_orgs + the plain-English doc + an `all_reviewed` flag (drives collapsed-by-default
    rendering: a fully-reviewed group is collapsed). Sorted most-unreviewed first, then most recent."""
    sev_rank = {"error": 3, "warning": 2, "info": 1}
    groups = {}
    for r in (rows or []):
        kind = r.get("category") or "other"
        g = groups.get(kind)
        if g is None:
            meta = (kind_meta or {}).get(kind) or _kind_fallback(kind)
            g = groups[kind] = {
                "kind": kind, "label": meta.get("label") or kind,
                "module": meta.get("module") or "admin", "known": bool(meta.get("known", True)),
                "doc": {"layman_meaning": meta.get("layman_meaning"), "layman_fix": meta.get("layman_fix"),
                        "escalate_when": meta.get("escalate_when"), "code_hint": meta.get("code_hint")},
                "count": 0, "unreviewed_count": 0, "reviewed_count": 0,
                "latest_at": None, "severity": "info", "sample_ids": [], "_orgs": {},
            }
        g["count"] += 1
        if r.get("reviewed"):
            g["reviewed_count"] += 1
        else:
            g["unreviewed_count"] += 1
        ca = r.get("created_at")
        if ca and (g["latest_at"] is None or str(ca) > str(g["latest_at"])):
            g["latest_at"] = ca
        if sev_rank.get(r.get("severity"), 0) > sev_rank.get(g["severity"], 0):
            g["severity"] = r.get("severity") or g["severity"]
        if r.get("id"):
            g["sample_ids"].append(r["id"])
        oid = r.get("org_id")
        if oid:
            g["_orgs"][oid] = g["_orgs"].get(oid, 0) + 1
    out = []
    for g in groups.values():
        g["all_reviewed"] = g["count"] > 0 and g["unreviewed_count"] == 0
        g["sample_ids"] = g["sample_ids"][:500]
        g["affected_orgs"] = [{"org_id": o, "count": c} for o, c in g.pop("_orgs").items()]
        out.append(g)
    out.sort(key=lambda x: (x["unreviewed_count"], str(x["latest_at"] or "")), reverse=True)
    return out


# ── Fix-request pipeline (mig 716) — pure decisions ───────────────────────────────────────────
FIX_STATUSES = ("new", "pending_approval", "approved", "in_progress", "resolved", "rejected")
FIX_APPROVAL_TARGETS = ("approved", "rejected")   # the approval gate — super_admin ONLY


def fix_status_change(current, target, is_super_admin):
    """PURE decision for a support_fix_request status transition. Returns (ok, reason). Only a super_admin
    may set 'approved' or 'rejected' (the approval gate); any support agent may move a request through the
    working states (new/pending_approval/in_progress/resolved). Unknown target → rejected. Unit-proven."""
    t = str(target or "").strip().lower()
    if t not in FIX_STATUSES:
        return (False, "invalid status")
    if t in FIX_APPROVAL_TARGETS and not is_super_admin:
        return (False, "only a super-admin can approve or reject a fix request")
    return (True, "")


# Statuses a NON-super-admin caller may set at CREATION time. Approval states are gated to super_admins
# here too (mirroring fix_status_change) so a house support agent can't POST status='approved' straight
# into the automation queue and bypass the transition-side approval gate.
FIX_SAFE_INITIAL_STATUSES = ("new", "pending_approval")


def _new_fix_request_row(body, *, org_id, created_by, sample_ids, affected_orgs, failure_count,
                         status="pending_approval", is_super_admin=False):
    """Build a support_fix_request insert row (shared by the tenant /core and house /support create paths).

    APPROVAL GATE AT CREATION: only a super_admin may create a request already in an approval state
    ('approved'/'rejected'). For every other caller the initial status is clamped to a safe pre-approval
    state (FIX_SAFE_INITIAL_STATUSES) — default-DENY — so the transition-side super_admin gate can't be
    bypassed by POSTing status='approved' at creation. A super_admin who does create an approval-state row
    is stamped approved_by/approved_at for audit parity with the /status transition path."""
    now = datetime.now(timezone.utc).isoformat()
    req = str(status or "").strip().lower()
    if req not in FIX_STATUSES:
        req = "pending_approval"
    if not is_super_admin and req not in FIX_SAFE_INITIAL_STATUSES:
        req = "pending_approval"                    # non-super may never enter an approval (or other non-safe) state
    row = {
        "org_id": org_id, "kind": (body.get("kind") or None), "module": (body.get("module") or None),
        "title": (str(body.get("title") or body.get("kind") or "Fix request"))[:300],
        "summary": (body.get("summary") or None), "proposed_action": (body.get("proposed_action") or None),
        "code_hint": (body.get("code_hint") or None),
        "sample_failure_ids": list(sample_ids or []), "affected_orgs": list(affected_orgs or []),
        "failure_count": int(failure_count or 0),
        "status": req,
        "created_by": created_by, "created_at": now, "updated_at": now,
    }
    if req in FIX_APPROVAL_TARGETS:                  # only reachable for a super_admin — stamp the audit trail
        row["approved_by"] = created_by
        row["approved_at"] = now
    return row


def _fetch_failures(client, *, org_id=None, reviewed="", category=None, ids=None,
                    date_from=None, date_to=None, limit=1000):
    """Fetch core.failure_log rows with GRACEFUL degradation if the mig-716 `reviewed` column is absent
    (retries without the reviewed filter and filters in Python). org_id=None → CROSS-TENANT (the caller MUST
    be house-gated — used only by the support console). Shared by /core and /support."""
    def build(with_reviewed):
        q = client.schema("core").table("failure_log").select("*")
        if org_id:
            q = q.eq("org_id", org_id)
        if category:
            q = q.eq("category", category)
        if ids:
            q = q.in_("id", list(ids))
        if date_from:
            q = q.gte("created_at", date_from)
        if date_to:
            q = q.lte("created_at", date_to)
        if with_reviewed and reviewed in ("true", "false"):
            q = q.eq("reviewed", reviewed == "true")
        return q.order("created_at", desc=True).limit(min(max(int(limit or 1), 1), 3000))
    try:
        return build(True).execute().data or []
    except Exception:
        rows = build(False).execute().data or []   # mig 716 un-run → no `reviewed` column; filter in Python
        if reviewed in ("true", "false"):
            want = (reviewed == "true")
            rows = [r for r in rows if bool(r.get("reviewed", False)) == want]
        return rows


def _house_kind_docs(client):
    """The HOUSE (global) failure_kind_doc rows, best-effort (mig un-run → [])."""
    try:
        return (client.schema("core").table("failure_kind_doc").select("*")
                .eq("org_id", ORG_ID).execute().data) or []
    except Exception:
        return []


def _can_view_failures(caller):
    """Admin-only by default. A role can be GRANTED the module via the /failures page override (RBAC)."""
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    pg = (perms.get("pages") or {}).get("/failures")
    if pg is not None:
        return bool(pg)
    return perms.get("scope") == "all" or (caller.get("role") or "").lower() == "admin"


@router.get("/failure-types")
def failure_types(authorization: str = Header(default="")):
    """The registry of known failure categories + default remediation (drives filters / UI)."""
    return {"types": [{"key": k, "label": v["label"], "severity": v["severity"],
                       "remediation": v["remediation"]} for k, v in FAILURE_TYPES.items()]}


@router.post("/failures")
def record_failure(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Record a failure (best-effort — never raises). Any authed surface may call it; org comes from the
    caller (falls back to body/house org for system callers). Remediation is auto-filled by category, and a
    category the tenant has DISABLED is silently skipped (configurable)."""
    uid = _uid_from_token(authorization)
    client = sb()
    caller = _resolve_caller(client, uid, x_active_org) if uid else None
    org_id = (caller["org_id"] if caller else None) or body.get("org_id") or ORG_ID
    category = (body.get("category") or "other").strip().lower()
    try:
        t = _tenant_row(client, org_id) or {}
        disabled = [str(d).strip().lower() for d in (t.get("failure_log_disabled_categories") or [])]
    except Exception:
        disabled = []
    if category in disabled:
        return {"ok": True, "logged": False, "reason": "category disabled"}
    meta = FAILURE_TYPES.get(category, FAILURE_TYPES["other"])
    row = {
        "org_id": org_id, "category": category,
        "severity": (body.get("severity") or meta["severity"]),
        "source": (body.get("source") or "")[:200] or None,
        "employee_id": body.get("employee_id"),
        "employee_name": (body.get("employee_name") or "")[:200] or None,
        "store_code": (body.get("store_code") or "")[:80] or None,
        "message": (body.get("message") or meta["label"])[:1000],
        "detail": body.get("detail"),
        "remediation": body.get("remediation") or meta["remediation"],
    }
    try:
        r = client.schema("core").table("failure_log").insert(row).execute()
        return {"ok": True, "logged": True, "id": (r.data[0]["id"] if r.data else None)}
    except Exception as e:
        return {"ok": False, "logged": False, "error": str(e)}


@router.get("/failures")
def list_failures(authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                        status: str = "", category: str = "", reviewed: str = "", limit: int = 300):
    """Flat list (drives the export + detail table). `reviewed` = '' (all) | 'false' (unreviewed — the
    default triage view) | 'true'. Degrades gracefully if mig 716's `reviewed` column is un-run."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    caller = _resolve_caller(client, uid, x_active_org)
    if not _can_view_failures(caller):
        raise HTTPException(403, "Failure Logs are admin-only. Grant the /failures page to a role to share it.")
    try:
        rows = _fetch_failures(client, org_id=caller["org_id"], reviewed=reviewed,
                               category=(category or None), limit=min(max(limit, 1), 1000))
    except Exception as e:
        raise HTTPException(500, f"failure_log unavailable (run migration 112?): {e}")
    if status:
        rows = [r for r in rows if r.get("status") == status]
    open_n = sum(1 for r in rows if r.get("status") == "open")
    unreviewed_n = sum(1 for r in rows if not r.get("reviewed"))
    return {"failures": rows, "open_count": open_n, "unreviewed_count": unreviewed_n,
            "can_configure": _can_edit_setting(caller, "failures")}


@router.patch("/failures/{fid}")
def update_failure(fid: str, body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    caller = _resolve_caller(client, uid, x_active_org)
    if not _can_view_failures(caller):
        raise HTTPException(403, "admin only")
    patch = {}
    if "status" in body:
        patch["status"] = (body.get("status") or "open").strip().lower()
        patch["resolved_at"] = datetime.now(timezone.utc).isoformat() if patch["status"] != "open" else None
        patch["resolved_by"] = (caller.get("role") or "admin")
    if "resolved_note" in body:
        patch["resolved_note"] = body.get("resolved_note")
    if not patch:
        raise HTTPException(400, "nothing to update")
    client.schema("core").table("failure_log").update(patch).eq("org_id", caller["org_id"]).eq("id", fid).execute()
    return {"ok": True, "id": fid}


@router.get("/failures/config")
def get_failures_config(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    caller = _resolve_caller(client, uid, x_active_org)
    if not _can_view_failures(caller):
        raise HTTPException(403, "admin only")
    t = _tenant_row(client, caller["org_id"]) or {}
    thr = t.get("face_match_threshold")
    return {
        "face_match_threshold": float(thr) if thr is not None else 0.60,
        "disabled_categories": t.get("failure_log_disabled_categories") or [],
        "types": [{"key": k, "label": v["label"]} for k, v in FAILURE_TYPES.items()],
        "can_configure": _can_edit_setting(caller, "failures"),
    }


@router.put("/failures/config")
def put_failures_config(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    caller = _resolve_caller(client, uid, x_active_org)
    if not _can_edit_setting(caller, "failures"):
        raise HTTPException(403, "you don't have permission to configure Failure Logs")
    patch = {}
    if "face_match_threshold" in body:
        try:
            v = float(body["face_match_threshold"])
        except (TypeError, ValueError):
            raise HTTPException(400, "face_match_threshold must be a number")
        patch["face_match_threshold"] = max(0.45, min(0.72, v))  # clamp to a safe band
    if "disabled_categories" in body:
        dc = body.get("disabled_categories") or []
        patch["failure_log_disabled_categories"] = [str(x).strip().lower() for x in dc if str(x).strip()]
    if not patch:
        raise HTTPException(400, "nothing to update")
    client.schema("storeops").table("tenants").update(patch).eq("org_id", caller["org_id"]).execute()
    return {"ok": True, **patch}


# ── Failure TRIAGE (mig 716): plain-English registry · grouped view · bulk-review · fix requests ──
@router.get("/failure-kind-docs")
def failure_kind_docs(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The MERGED plain-English how-to-fix registry (editable DB rows over the in-code fallback). Readable
    by any signed-in user (the /failures + support pages render "what this means" + "how to fix it" from
    it). `can_edit` is true only for house support staff (they edit the GLOBAL registry)."""
    client = sb()
    rows = [r for r in _house_kind_docs(client) if r.get("is_active", True)]
    merged = _merge_kind_docs(rows)
    return {"kinds": list(merged.values()), "can_edit": _support_gate(authorization, x_active_org)}


@router.post("/failure-kind-docs")
def upsert_failure_kind_doc(body: dict, authorization: str = Header(default=""),
                                  x_active_org: str = Header(default="")):
    """Create/update one plain-English kind doc in the HOUSE global registry. Support-gated (the SAME gate
    as the support docs editor). Keyed by (org_id, kind)."""
    if not _support_gate(authorization, x_active_org):
        raise HTTPException(403, "Editing the plain-English error registry is restricted to house support staff.")
    kind = (body.get("kind") or "").strip().lower()
    if not kind:
        raise HTTPException(422, "kind is required")
    row = {"org_id": ORG_ID, "kind": kind, "updated_by": (body.get("updated_by") or None),
           "updated_at": datetime.now(timezone.utc).isoformat()}
    for f in (*_KIND_DOC_FIELDS, "is_active"):
        if f in body:
            row[f] = body[f]
    try:
        sb().schema("core").table("failure_kind_doc").upsert(row, on_conflict="org_id,kind").execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 716 first: {e}")
    return {"ok": True, "kind": kind}


@router.get("/failures/grouped")
def failures_grouped(authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                           reviewed: str = "false", category: str = "", limit: int = 1500):
    """Grouped TRIAGE view for THIS tenant (admin-gated, org-scoped). Groups similar failures by kind with
    count / latest / unreviewed_count + the plain-English doc, so the UI renders collapsible groups and
    clears a whole group at once. `reviewed` defaults to 'false' (the unreviewed queue)."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    caller = _resolve_caller(client, uid, x_active_org)
    if not _can_view_failures(caller):
        raise HTTPException(403, "Failure Logs are admin-only. Grant the /failures page to a role to share it.")
    try:
        rows = _fetch_failures(client, org_id=caller["org_id"], reviewed=reviewed,
                               category=(category or None), limit=limit)
    except Exception as e:
        raise HTTPException(500, f"failure_log unavailable (run migration 112/716?): {e}")
    kind_meta = _merge_kind_docs(_house_kind_docs(client))
    groups = _build_failure_groups(rows, kind_meta)
    return {"groups": groups, "total": len(rows),
            "unreviewed_total": sum(g["unreviewed_count"] for g in groups),
            "can_configure": _can_edit_setting(caller, "failures")}


@router.post("/failures/bulk-review")
def failures_bulk_review(body: dict, authorization: str = Header(default=""),
                               x_active_org: str = Header(default="")):
    """The CLEAR action: mark selected failure rows reviewed (or un-reviewed) — keeps the rows for the audit
    trail, org-scoped to the caller's tenant. body: {ids:[...], reviewed:true|false}."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    caller = _resolve_caller(client, uid, x_active_org)
    if not _can_view_failures(caller):
        raise HTTPException(403, "Failure Logs are admin-only.")
    ids = [str(i) for i in (body.get("ids") or []) if i]
    if not ids:
        raise HTTPException(422, "ids[] required")
    reviewed = bool(body.get("reviewed", True))
    patch = {"reviewed": reviewed,
             "reviewed_by": ((caller.get("role") or "admin") if reviewed else None),
             "reviewed_at": (datetime.now(timezone.utc).isoformat() if reviewed else None)}
    try:
        (client.schema("core").table("failure_log").update(patch)
         .eq("org_id", caller["org_id"]).in_("id", ids).execute())
    except Exception as e:
        raise HTTPException(500, f"could not update (run migration 716?): {e}")
    return {"ok": True, "reviewed": reviewed, "count": len(ids)}


@router.post("/fix-requests")
def create_fix_request(body: dict, authorization: str = Header(default=""),
                             x_active_org: str = Header(default="")):
    """Club a group of similar failures (from /failures) into ONE fix request for THIS tenant. Admin-gated,
    org-scoped. Enters the pipeline at 'pending_approval' → a super-admin approves it in the support console.
    Does NOT touch any failure row (the club is a reference by id) and NEVER edits code or data."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    caller = _resolve_caller(client, uid, x_active_org)
    if not _can_view_failures(caller):
        raise HTTPException(403, "Failure Logs are admin-only.")
    org_id = caller["org_id"]
    ids = [str(i) for i in (body.get("sample_failure_ids") or []) if i]
    row = _new_fix_request_row(body, org_id=org_id, created_by=(caller.get("role") or "admin"),
                               sample_ids=ids, affected_orgs=[{"org_id": org_id, "count": len(ids)}],
                               failure_count=int(body.get("failure_count") or len(ids)))
    try:
        r = client.schema("storeops").table("support_fix_request").insert(row).execute()
        return {"ok": True, "id": (r.data[0]["id"] if r.data else None), "status": row["status"]}
    except Exception as e:
        raise HTTPException(500, f"could not create fix request (run migration 716?): {e}")


@router.get("/fix-requests")
def list_fix_requests(authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                            status: str = ""):
    """This tenant's fix requests (admin-gated, org-scoped)."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    caller = _resolve_caller(client, uid, x_active_org)
    if not _can_view_failures(caller):
        raise HTTPException(403, "Failure Logs are admin-only.")
    q = client.schema("storeops").table("support_fix_request").select("*").eq("org_id", caller["org_id"])
    if status:
        q = q.eq("status", status)
    try:
        rows = q.order("created_at", desc=True).limit(500).execute().data or []
    except Exception:
        rows = []
    return {"fix_requests": rows, "statuses": list(FIX_STATUSES)}


@router.get("/tenant-settings")
def get_tenant_settings(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The signed-in user's OWN tenant pay-period settings + a worked example of upcoming periods.
    Any signed-in user may read; only an admin may write (PUT)."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    caller = _resolve_caller(client, uid, x_active_org)
    if not caller:
        raise HTTPException(403, "no tenant for this login")
    org_id = caller["org_id"]
    t = _tenant_row(client, org_id) or {}
    s = _pp_settings(t)
    return {"org_id": org_id, "name": t.get("name"), "settings": s,
            "setup_complete": bool(t.get("setup_complete")),
            "can_edit": _can_edit_setting(caller, "pay_period"),
            "preview": _next_periods(s)}


@router.put("/tenant-settings")
def put_tenant_settings(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The tenant ADMIN defines/updates the pay period (captured at onboarding). Saving a complete,
    valid definition marks the tenant setup_complete (clears the setup banner). Super-admins may pass
    org_id to set it for any tenant; otherwise it targets the caller's own tenant."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    caller = _resolve_caller(client, uid, x_active_org)
    if not caller:
        raise HTTPException(403, "no tenant for this login")
    org_id = (body.get("org_id") if caller["super_admin"] else None) or caller["org_id"] or ORG_ID
    if not _can_edit_setting(caller, "pay_period"):
        raise HTTPException(403, "you don't have permission to edit pay-period settings")
    upd = {}
    for k in _PP_FIELDS:
        if k in body:
            v = body[k]
            if k in ("work_week_start_dow", "payday_dow", "payday_weeks_after"):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    raise HTTPException(400, f"{k} must be a number")
            if k == "biweekly_anchor" and not (v or "").strip():
                v = None
            upd[k] = v
    if not upd:
        raise HTTPException(400, "nothing to update")
    # A weekly/biweekly type + a start dow is enough to be 'complete'.
    merged = _pp_settings({**(_tenant_row(client, org_id) or {}), **upd})
    if merged["pay_period_type"] in ("weekly", "biweekly"):
        upd["setup_complete"] = True
        upd["setup_completed_at"] = datetime.now(timezone.utc).isoformat()
    client.schema("storeops").table("tenants").update(upd).eq("org_id", org_id).execute()
    return {"ok": True, "settings": merged, "preview": _next_periods(merged)}


# ── Roles ────────────────────────────────────────────────────────────────────────────
def _level_role_perms(rank: int) -> dict:
    """Starting permissions for an org-level-derived role, scaled by org depth. The admin then
    tunes them on the Role Permissions tab. RBAC is gated OFF until enforce-login is on, so these
    grant nothing until the admin both assigns the role AND turns enforcement on."""
    def M(**on):
        # Same ONE key universe as _mods (platform-core-3): canonical modules + role-gate keys. A key
        # present with False ≡ absent for every RBAC consumer, so this is behavior-neutral.
        base = {k: False for k in (*MODULE_CATALOG.keys(), *ROLE_GATE_KEYS)}
        base.update(on)
        return base
    def R(on):  # per-area REPORT access (separate from the operational module)
        return {k: on for k in ("commissions", "asset", "vip", "accounts", "storeops", "closing")}
    # `closing` (Daily Closing) rides with `storeops` for every store-operations tier — same rationale as
    # _BASE_ROLES. FORWARD-ONLY (INSERT of a level-derived role); never rewrites an existing role row.
    if rank <= 1:    # Executive / Director — company-wide leadership: full reports + HR
        return {"modules": M(commissions=True, targets=True, asset=True, vip=True, storeops=True, closing=True, hr=True, notify=True),
                "reports": R(True), "scope": "all", "home": "/commcalc"}
    if rank <= 3:    # Regional / District manager — market scope: operational only, NO reports by default
        return {"modules": M(commissions=True, targets=True, asset=True, storeops=True, closing=True, notify=True),
                "reports": R(False), "scope": "market", "home": "/commcalc/targets"}
    if rank == 4:    # Store manager — store scope: NO reports by default
        return {"modules": M(commissions=True, targets=True, asset=True, storeops=True, closing=True),
                "reports": R(False), "scope": "store", "home": "/commcalc/targets"}
    return {"modules": M(targets=True), "reports": R(False), "scope": "self", "home": "/commcalc/targets/my"}  # rep


def _ensure_roles_for_levels(client, org_id: str) -> None:
    """Make every org-chart level (Executive, Director, Regional/District Manager, …) an
    assignable, editable access role so it shows up in the Roles & Access dropdown. Idempotent:
    only INSERTS a role for a level that has no matching role name yet — never updates/clobbers an
    existing (possibly admin-edited) role. Levels that already match a seeded role (e.g. a level
    named to collide with 'store_manager') are left alone."""
    levels = (client.schema("storeops").table("org_levels")
              .select("name,rank").eq("org_id", org_id).execute().data or [])
    if not levels:
        return
    existing = {r["name"] for r in (client.schema("storeops").table("roles")
                .select("name").eq("org_id", org_id).execute().data or [])}
    new_rows, seen = [], set()
    for lv in levels:
        nm = re.sub(r"[^a-z0-9]+", "_", (lv.get("name") or "").lower()).strip("_")
        if not nm or nm in existing or nm in seen:
            continue
        seen.add(nm)
        new_rows.append({"org_id": org_id, "name": nm, "display_name": lv.get("name"),
                         "permissions": _level_role_perms(lv.get("rank") or 0)})
    if new_rows:
        client.schema("storeops").table("roles").insert(new_rows).execute()


@router.get("/roles")
def list_roles(org_id: str = ORG_ID):
    client = sb()
    try:
        _ensure_roles_for_levels(client, org_id)   # org-chart levels become assignable roles
    except Exception:
        pass  # never block the roles list if the level→role sync fails
    rows = client.schema("storeops").table("roles").select("*").eq("org_id", org_id) \
        .order("id").execute().data or []
    return {"roles": rows}


@router.post("/roles")
def create_role(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                x_active_org: str = Header(default="")):
    _require_setting(authorization, x_active_org, "security")
    name = (body.get("name") or "").strip().lower().replace(" ", "_")
    if not name:
        raise HTTPException(400, "name required")
    row = {"org_id": org_id, "name": name,
           "display_name": body.get("display_name") or name.replace("_", " ").title(),
           "permissions": body.get("permissions") or {}}
    res = sb().schema("storeops").table("roles").upsert(row, on_conflict="org_id,name").execute()
    return (res.data or [{}])[0]


@router.put("/roles/{role_id}")
def update_role(role_id: int, body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                x_active_org: str = Header(default="")):
    _require_setting(authorization, x_active_org, "security")
    upd = {}
    if "display_name" in body:
        upd["display_name"] = body["display_name"]
    if "permissions" in body:
        upd["permissions"] = body["permissions"]
    if not upd:
        raise HTTPException(400, "nothing to update")
    # role_id is a GLOBAL primary key (BIGSERIAL, enumerable 1,2,3…). WITHOUT an org filter the old
    # handler let a caller rewrite ANOTHER TENANT's role permissions by guessing an integer id —
    # cross-tenant privilege escalation. org_id is the tenant-middleware-rewritten query param (a
    # normal admin cannot forge it; a super-admin's chosen org_id is honored for legitimate
    # cross-tenant admin), so scoping the UPDATE to it is correct for every caller.
    res = (sb().schema("storeops").table("roles").update(upd)
           .eq("id", role_id).eq("org_id", org_id).execute())
    if not res.data:
        raise HTTPException(404, "role not found")
    return res.data[0]


@router.delete("/roles/{role_id}")
def delete_role(role_id: int, org_id: str = ORG_ID, authorization: str = Header(default=""),
                x_active_org: str = Header(default="")):
    """Delete a custom role. Refuses to delete 'admin' (lock-out guard) and blocks deletion while any
    user is still assigned it (reassign them first) so nobody is silently orphaned."""
    _require_setting(authorization, x_active_org, "security")
    client = sb()
    rows = (client.schema("storeops").table("roles").select("id,name")
            .eq("org_id", org_id).eq("id", role_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "role not found")
    name = rows[0].get("name")
    if name == "admin":
        raise HTTPException(400, "The admin role can't be deleted.")
    try:
        used = (client.schema("storeops").table("app_users").select("id")
                .eq("org_id", org_id).eq("role", name).limit(1).execute().data) or []
    except Exception:
        used = []
    if used:
        raise HTTPException(400, f"'{name}' is still assigned to at least one user — reassign them first.")
    client.schema("storeops").table("roles").delete().eq("org_id", org_id).eq("id", role_id).execute()
    return {"ok": True, "deleted": name}


# ── Users (app accounts) + provisioning ────────────────────────────────────────────────
@router.get("/users")
def list_users(org_id: str = ORG_ID):
    """All app users (core.users) with their role + login state. Degrades to [] if migration
    015 hasn't run (so the admin page doesn't hard-error out of order)."""
    try:
        rows = sb().schema("storeops").table("app_users").select("*") \
            .eq("org_id", org_id).order("full_name").execute().data or []
    except Exception:
        return {"users": [], "ready": False}
    for r in rows:
        r["has_login"] = bool(r.get("auth_id"))
    return {"users": rows, "ready": True}


def _ensure_employee(client, org_id, email, full_name=None, store_code=None, emp_emails=None):
    """Make sure a storeops.employees row exists for a person added via Roles, so they
    propagate to the Employee list and become editable there. No-op if one already exists
    (matched by email). Returns True if a new row was created."""
    email = (email or "").strip().lower()
    if not email:
        return False
    if emp_emails is None:
        existing = client.schema("storeops").table("employees").select("email") \
            .eq("org_id", org_id).execute().data or []
        emp_emails = {(e.get("email") or "").lower() for e in existing if e.get("email")}
    if email in emp_emails:
        return False
    try:
        client.schema("storeops").table("employees").insert({
            "org_id": org_id, "name": (full_name or email), "email": email,
            "home_store": store_code or None, "is_active": True,
        }).execute()
        emp_emails.add(email)
        return True
    except Exception:
        return False


@router.get("/employees")
async def list_employees(org_id: str = ORG_ID):
    """The storeops.employees roster + whether each already has an app login + assigned role.
    Drives the assignment grid (assign a role, then create logins)."""
    client = sb()
    emps = client.schema("storeops").table("employees").select(
        "id,employee_id,name,home_store,role,pay_rate,email,phone,is_active") \
        .eq("org_id", org_id).order("name").execute().data or []
    try:
        users = client.schema("storeops").table("app_users").select("*").eq("org_id", org_id) \
            .execute().data or []
    except Exception:
        users = []   # migration 015 not run yet → no assignments to merge

    # Backfill: any app_user with no matching employee gets a roster row, so manually-added
    # Roles users show up in the Employee list and are editable (not stuck as synthetic rows).
    emp_emails = {(e.get("email") or "").lower() for e in emps if e.get("email")}
    emp_eids = {e.get("employee_id") for e in emps if e.get("employee_id")}
    created = 0
    for u in users:
        ue = (u.get("email") or "").lower()
        if not ue or ue in emp_emails:
            continue
        if u.get("employee_id") and u.get("employee_id") in emp_eids:
            continue
        if _ensure_employee(client, org_id, ue, u.get("full_name"), u.get("store_code"), emp_emails):
            created += 1
    if created:
        emps = client.schema("storeops").table("employees").select(
            "id,employee_id,name,home_store,role,pay_rate,email,phone,is_active") \
            .eq("org_id", org_id).order("name").execute().data or []
    by_email = {(u.get("email") or "").lower(): u for u in users if u.get("email")}
    by_emp = {u.get("employee_id"): u for u in users if u.get("employee_id")}
    # Pending account-link invites for THIS tenant (the admin's own data — no cross-tenant read). Used
    # to show a UNIFORM "invited" state (platform-core-11): a pending invite and a freshly-created
    # login that hasn't signed in yet both render "invited" until the user completes access, so the
    # roster never reveals whether an email already exists in another tenant (anti-enumeration).
    pending_emails = set()
    try:
        pinv = (client.schema("core").table("account_link_invite").select("email")
                .eq("org_id", org_id).eq("status", "pending").execute().data) or []
        pending_emails = {(r.get("email") or "").lower() for r in pinv if r.get("email")}
    except Exception:
        pending_emails = set()

    def _login_state(u, email):
        """Uniform status: 'active' once the person has actually signed in for this tenant; else
        'invited' if a login was created OR an invite is pending; else '' (no login/invite)."""
        auth = bool((u or {}).get("auth_id"))
        active = auth and bool((u or {}).get("last_login"))
        invited = auth or ((email or "").lower() in pending_emails)
        return {"has_login": auth, "login_active": active,
                "login_status": ("active" if active else ("invited" if invited else ""))}

    out = []
    matched = set()
    for e in emps:
        u = by_emp.get(e.get("employee_id")) or by_email.get((e.get("email") or "").lower())
        if u:
            matched.add(u.get("id"))
        out.append({
            **e,
            "app_role": (u or {}).get("role"),
            **_login_state(u, e.get("email")),
            "app_market": (u or {}).get("market"),
            "app_store": (u or {}).get("store_code"),
            "app_store_codes": (u or {}).get("store_codes"),   # floaters: full store set
            "widget_overrides": (u or {}).get("widget_overrides"),
        })
    # Surface app_users that have no matching employee (manually added via "Add a person")
    # so they stay visible/manageable. Negative synthetic ids never collide with employee ids.
    syn = 0
    for u in users:
        if u.get("id") in matched:
            continue
        syn += 1
        out.append({
            "id": -syn,
            "employee_id": u.get("employee_id"),
            "name": u.get("full_name") or u.get("email"),
            "home_store": None, "role": None, "phone": None, "pay_rate": None,
            "email": u.get("email"),
            "is_active": u.get("is_active", True),
            "app_role": u.get("role"),
            **_login_state(u, u.get("email")),
            "app_market": u.get("market"),
            "app_store": u.get("store_code"),
            "app_store_codes": u.get("store_codes"),   # floaters: full store set
            "widget_overrides": u.get("widget_overrides"),
            "manual": True,
        })
    return {"employees": out, "with_email": sum(1 for e in emps if (e.get("email") or "").strip())}


@router.get("/filter-options")
def filter_options(org_id: str = ORG_ID):
    """Org-scoped option source for the shared StandardFilterBar (RULE FIVE §3d): the tenant's stores
    (+their markets), distinct markets, and the rep/employee roster (union of the storeops roster and any
    app_users). PICK-DON'T-TYPE source — every value is a real org row, so a filter can never reference
    data outside the tenant. Each source is best-effort (a missing table/migration → that list is just
    empty; the endpoint never 500s). NOT heavy: stores/employees/store_mapping are small config tables.
    Reps are 'First Last' with an email sublabel for same-name disambiguation (§3b)."""
    client = sb()
    stores: dict[str, str | None] = {}   # store display key → market
    markets: set[str] = set()

    def _add_store(key, market):
        key = (key or "").strip()
        if not key:
            return
        market = (market or "").strip() or None
        if market:
            markets.add(market)
        # keep the first non-null market seen for a store
        if key not in stores or (stores[key] is None and market):
            stores[key] = market

    # 1) storeops.stores — the canonical tenant store list (address is the report store key; carries market)
    try:
        for r in (client.schema("storeops").table("stores")
                  .select("store_code,address,market").eq("org_id", org_id)
                  .limit(5000).execute().data or []):
            _add_store(r.get("address") or r.get("store_code"), r.get("market"))
    except Exception:
        pass
    # 2) commcalc.store_mapping — market map / any stores not in storeops.stores (best-effort)
    try:
        for r in (client.schema("commcalc").table("store_mapping")
                  .select("store_address,market").eq("org_id", org_id)
                  .limit(5000).execute().data or []):
            _add_store(r.get("store_address"), r.get("market"))
    except Exception:
        pass

    # 3) reps/employees — the org roster (storeops.employees) ∪ app_users; 'First Last' + email sublabel.
    reps: dict[str, str] = {}   # name → email (first non-empty wins)
    try:
        for e in (client.schema("storeops").table("employees")
                  .select("name,email,is_active").eq("org_id", org_id)
                  .limit(10000).execute().data or []):
            nm = (e.get("name") or "").strip()
            if nm and nm not in reps:
                reps[nm] = (e.get("email") or "").strip()
    except Exception:
        pass
    try:
        for u in (client.schema("storeops").table("app_users")
                  .select("full_name,email").eq("org_id", org_id)
                  .limit(10000).execute().data or []):
            nm = (u.get("full_name") or "").strip()
            if nm and nm not in reps:
                reps[nm] = (u.get("email") or "").strip()
    except Exception:
        pass

    store_list = sorted(({"store": k, "market": v} for k, v in stores.items()), key=lambda x: x["store"])
    rep_list = [
        ({"id": nm, "label": nm, "sublabel": em} if em else {"id": nm, "label": nm})
        for nm, em in sorted(reps.items(), key=lambda kv: kv[0].lower())
    ]
    return {"stores": store_list, "markets": sorted(markets), "reps": rep_list}


# ── Grant universe + scope diagnostic (2026-08-03 reporting-vs-scheduling scope split) ───────────
# WHY these live in core and not in a module: they are the option source for a GRANT, and a grant is
# an identity/RBAC concept. The /admin/roles market+store pickers used to source from
# GET /storeops/stores, which is (a) itself SPAN-SCOPED — so the person handing out grants could only
# offer the markets they personally cover — and (b) sourced from storeops.stores.market ALONE, while
# the tenant's real market vocabulary is the UNION of storeops.stores.market and
# commcalc.store_mapping.market. Result reported by the owner on 2026-08-03: "the option to select PA
# from the roles and config is not there" — PA existed only in commcalc.store_mapping.
# app/core/scope.market_index() is the ONE canonical union, and it is the SAME function that RESOLVES
# a market grant to its member stores, so the picker can never offer a market the resolver cannot bind.
@router.get("/markets")
def grant_universe(org_id: str = ORG_ID):
    """Canonical org-scoped GRANT universe for the roles/config market + store pickers (RULE THREE:
    pick-don't-type). Deliberately NOT span-scoped — you cannot delegate a market you are forbidden
    to see, and an admin assigning a DM to 3 markets must be able to see all of the tenant's markets.
    Org isolation (the real boundary) is enforced by tenant_middleware rewriting org_id.

    Returns {"markets": [canonical names], "stores": [{store_code, address, market}]}. Best-effort
    per source — a missing table degrades that half to empty, never a 500."""
    idx = _scope.market_index(sb(), org_id)
    return {"markets": idx.get("markets") or [], "stores": idx.get("stores") or []}


@router.get("/scope-preview")
def scope_preview(role: str = "", email: str = "", org_id: str = ORG_ID,
                        authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """DIAGNOSTIC: show what a role and/or a specific login actually resolves to, split into the two
    independent questions. Read-only; changes nothing.

      reporting.stores  — whose NUMBERS they may see (the store grant, market grants RESOLVED)
      scheduling.reach  — whom they may put on a shift ('org' = any employee in the tenant)

    This is the surface that answers "I gave the DM 3 markets, why do they see everything?" without
    anyone having to log in as them. Un-granted markets are listed under `unresolved_markets` — that
    is the tell for a market spelled one way on the app_user and another way on the stores.

    GATED: it reports another person's grants, so it is admin-only (super_admin, the 'admin' role, or
    any full-scope role) — the same bar as the /admin/roles page it is built for. Unlike
    /core/markets (a grant OPTION list) this is a grant DISCLOSURE, so it never runs anonymously."""
    client = sb()
    uid = _uid_from_token(authorization)
    caller = _resolve_caller(client, uid, x_active_org) if uid else None
    if not caller:
        raise HTTPException(401, "not authenticated")
    _cp = caller.get("perms") or {}
    if not (caller.get("super_admin") or _cp.get("scope") == "all"
            or (_cp.get("modules") or {}).get("admin")
            or (caller.get("role") or "").lower() == "admin"):
        raise HTTPException(403, "admin only")
    role = (role or "").strip()
    email = (email or "").strip().lower()
    perms, app_user = {}, None
    if email:
        rows = (client.schema("storeops").table("app_users")
                .select("email,full_name,role,employee_id,market,store_code,store_codes")
                .eq("org_id", org_id).eq("email", email).limit(1).execute().data) or []
        app_user = rows[0] if rows else None
        if app_user and not role:
            role = (app_user.get("role") or "").strip()
    if role:
        rr = (client.schema("storeops").table("roles").select("permissions")
              .eq("org_id", org_id).eq("name", role).limit(1).execute().data) or []
        if rr:
            perms = rr[0].get("permissions") or {}
    scope = (perms.get("scope") or "all")
    unit_codes = []
    eid = ((app_user or {}).get("employee_id") or "").strip()
    if eid:
        try:
            unit_codes = sorted({(r.get("store_code") or "").strip() for r in
                                 (client.rpc("org_span_for_manager",
                                             {"p_org_id": org_id, "p_employee_id": eid}).execute().data or [])
                                 if (r.get("store_code") or "").strip()})
        except Exception:
            unit_codes = []
    idx = _scope.market_index(client, org_id)
    granted = [m.strip() for m in str((app_user or {}).get("market") or "").split(",") if m.strip()]
    unresolved = [m for m in granted if not (idx.get("by_market") or {}).get(m.lower())]
    if scope == "all":
        reporting = {"unrestricted": True, "stores": [], "why": "role scope = all stores (company-wide)"}
    else:
        codes = _scope.reporting_span_codes(client, org_id, app_user, scope, org_unit_codes=unit_codes)
        reporting = {"unrestricted": False, "stores": sorted(codes),
                     "why": f"role scope = {scope}"}
    return {
        "role": role or None, "email": email or None, "scope": scope,
        "granted_markets": granted, "unresolved_markets": unresolved,
        "org_unit_stores": unit_codes,
        "pinned_stores": sorted({c for c in ([(app_user or {}).get("store_code")] +
                                             list((app_user or {}).get("store_codes") or []))
                                 if c and str(c).strip()}),
        "reporting": reporting,
        "scheduling": {"reach": _scope.scheduling_reach(perms),
                       "roster_span_exempt": _scope.roster_span_exempt(perms),
                       "why": ("'org' — may pick ANY employee in the tenant when scheduling "
                               "(reporting stays limited to the stores above)"
                               if _scope.roster_span_exempt(perms)
                               else "'span' — roster limited to the reporting stores above")},
        "org_markets": idx.get("markets") or [],
    }


@router.post("/users/assign")
async def assign_role(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                      x_active_org: str = Header(default="")):
    """Upsert a core.users row (assign role + scope). Keyed on (org_id, email). Does NOT create
    the auth login — call /users/create-login (or bulk-provision) for that."""
    _require_setting(authorization, x_active_org, "security")
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    role = (body.get("role") or "").strip()
    client = sb()
    cur = client.schema("storeops").table("app_users").select("*").eq("org_id", org_id) \
        .eq("email", email).limit(1).execute().data or []
    row = {
        "org_id": org_id, "email": email,
        "full_name": body.get("full_name") or (cur[0].get("full_name") if cur else None),
        "role": role or (cur[0].get("role") if cur else "sales_rep"),
        "market": body.get("market"),
        "store_code": body.get("store_code"),
        "store_codes": body.get("store_codes"),
        "employee_id": body.get("employee_id") or (cur[0].get("employee_id") if cur else None),
        "is_active": body.get("is_active", True if not cur else cur[0].get("is_active")),
    }
    if cur:
        res = client.schema("storeops").table("app_users").update(row).eq("id", cur[0]["id"]).execute()
    else:
        res = client.schema("storeops").table("app_users").insert(row).execute()
    # Propagate to the Employee roster so the person is editable in the Employee list too.
    _ensure_employee(client, org_id, email, row["full_name"], row["store_code"])
    return (res.data or [{}])[0]


@router.post("/users/bulk-assign")
def bulk_assign(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                x_active_org: str = Header(default="")):
    """Bulk upsert app_users (assign roles) from a list — powers the employee-sheet upload and
    the multi-add form. Body: {users:[{email, full_name, role, market, store_code}]}. Does NOT
    create logins (call /users/bulk-provision or per-row create-login after). Role names are
    validated against storeops.roles; bad rows are reported, the rest still apply."""
    _require_setting(authorization, x_active_org, "security")
    users = body.get("users")
    if not isinstance(users, list) or not users:
        raise HTTPException(400, "users[] required")
    client = sb()
    valid = {r["name"] for r in (client.schema("storeops").table("roles")
             .select("name").eq("org_id", org_id).execute().data or [])}
    _existing_emp = client.schema("storeops").table("employees").select("email") \
        .eq("org_id", org_id).execute().data or []
    emp_emails = {(e.get("email") or "").lower() for e in _existing_emp if e.get("email")}
    assigned, errors = 0, []
    for i, u in enumerate(users):
        email = (u.get("email") or "").strip().lower()
        role = (u.get("role") or "").strip()
        if not email or "@" not in email:
            errors.append({"row": i + 1, "email": email, "error": "missing/invalid email"})
            continue
        if role and role not in valid:
            errors.append({"row": i + 1, "email": email, "error": f"unknown role '{role}'"})
            continue
        row = {
            "org_id": org_id, "email": email,
            "full_name": (u.get("full_name") or None),
            "role": role or "sales_rep",
            "market": (u.get("market") or None),
            "store_code": (u.get("store_code") or None),
            "is_active": True,
        }
        try:
            cur = client.schema("storeops").table("app_users").select("id") \
                .eq("org_id", org_id).eq("email", email).limit(1).execute().data or []
            if cur:
                client.schema("storeops").table("app_users").update(row).eq("id", cur[0]["id"]).execute()
            else:
                client.schema("storeops").table("app_users").insert(row).execute()
            _ensure_employee(client, org_id, email, row["full_name"], row["store_code"], emp_emails)
            assigned += 1
        except Exception as e:
            errors.append({"row": i + 1, "email": email, "error": str(e)})
    return {"assigned": assigned, "errors": errors, "total": len(users)}


def _find_auth_user_by_email(admin, email):
    """Look up an existing Supabase Auth user id by email. PAGINATES — GoTrue's list_users() returns
    only ONE page (~50 users), so an unpaginated scan missed anyone beyond page 1 once the org had
    >50 logins, breaking relink/reset/provision with 'already registered'. Robust to server per_page
    caps: it treats a short page (fewer rows than page 1) as the last page."""
    email = (email or "").strip().lower()
    if not email:
        return None

    def _match(users):
        for u in users:
            ue = getattr(u, "email", None) or (u.get("email") if isinstance(u, dict) else None)
            if ue and ue.lower() == email:
                return getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else None)
        return None

    try:
        page, size = 1, None
        while page <= 200:                       # up to 200 pages of headroom
            resp = admin.auth.admin.list_users(page=page, per_page=1000)
            users = resp if isinstance(resp, list) else (getattr(resp, "users", None) or [])
            if not users:
                break
            hit = _match(users)
            if hit:
                return hit
            if size is None:
                size = len(users)                # server's effective page size (may cap below 1000)
            if len(users) < size:                # short page ⇒ last page
                break
            page += 1
        return None
    except TypeError:
        # older client without page/per_page kwargs → single page only (pre-existing behavior)
        try:
            resp = admin.auth.admin.list_users()
            users = resp if isinstance(resp, list) else (getattr(resp, "users", None) or [])
            return _match(users)
        except Exception:
            return None
    except Exception:
        return None


def _create_or_link_auth(admin, email, temp_pw):
    """Create a Supabase Auth account (email confirmed) for `email`, or link the existing one.
    Returns (auth_id, created_bool, error_or_None)."""
    try:
        resp = admin.auth.admin.create_user({
            "email": email, "password": temp_pw, "email_confirm": True,
        })
        user = getattr(resp, "user", None) or resp
        auth_id = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
        if auth_id:
            return auth_id, True, None
        return None, False, "no id returned"
    except Exception as e:
        # already registered → find it and (re)set the temp password
        existing = _find_auth_user_by_email(admin, email)
        if existing:
            try:
                admin.auth.admin.update_user_by_id(existing, {"password": temp_pw})
            except Exception:
                pass
            return existing, False, None
        return None, False, str(e)[:200]


def _alias_email(email: str, slug: str, n: int = 0) -> str:
    """A tenant-distinct login alias via plus-addressing: local+slug@domain (reaches the same inbox,
    but is a SEPARATE login/auth account). n>0 disambiguates a collision."""
    local, _, domain = (email or "").partition("@")
    tag = re.sub(r"[^a-z0-9]+", "", (slug or "t").lower())[:20] or "t"
    return f"{local}+{tag if n == 0 else f'{tag}{n}'}@{domain}"


# ── Consent-based account linking (platform-core-11) ──────────────────────────────────────────────
# When an admin provisions an email that ALREADY has a MetricsPro login in another tenant we must NOT
# (a) create a second login, (b) mint a mig-088 alias, or (c) silently bind a shared membership (the
# wave-4 default). Instead we enter a PENDING-CONNECTION state and the create-login response is made
# BYTE-IDENTICAL to a brand-new email (anti-enumeration). The user resolves it themselves on next
# sign-in: CONNECT the tenant onto their existing login (mig 706 shared membership → the switcher
# applies) or DISABLE the old login and take a fresh one. Fresh emails keep today's direct-create path.
class PendingConnectionRequired(Exception):
    """Raised inside _provision_login when the email already has a MetricsPro login in ANOTHER tenant
    and separate_login was not requested. create_login catches it and records a pending invite
    instead of minting/binding anything — zero side effects on the existing account."""


def _email_login_state(client, email, org_id):
    """Read-only (NO side effects): does this email already have a bound login here / elsewhere?
    Returns (member_here, member_elsewhere). DB-first (indexed email lookup) → constant-ish time
    regardless of existence, so it is not a timing oracle, and — unlike _create_or_link_auth — it
    never touches the Supabase auth account (which would reset the existing user's password)."""
    email = (email or "").strip().lower()
    if not email:
        return (False, False)
    rows = (client.schema("storeops").table("app_users").select("org_id,auth_id")
            .eq("email", email).execute().data) or []
    member_here = any(r.get("auth_id") and r.get("org_id") == org_id for r in rows)
    member_elsewhere = any(r.get("auth_id") and r.get("org_id") != org_id for r in rows)
    return (member_here, member_elsewhere)


def _provision_decision(*, member_here, member_elsewhere, separate_login):
    """PURE decision for what create-login should do (unit-proven in prove_account_linking.py):
      'reset'   — email already has a login in THIS tenant → just (re)set its password.
      'fresh'   — email has no login anywhere → mint a new account + bind (today's direct path).
      'alias'   — email has a login elsewhere AND separate_login=True → mint a mig-088 tenant alias.
      'pending' — email has a login elsewhere, default → record a consent invite, mint/bind NOTHING.
    The enumeration-sensitive pair is fresh vs pending (both separate_login=False, differing only by
    member_elsewhere): create_login returns a byte-identical response shape for both."""
    if member_here:
        return "reset"
    if not member_elsewhere:
        return "fresh"
    if separate_login:
        return "alias"
    return "pending"


def _mint_tenant_alias(client, admin, cur_row, org_id, email, temp_pw):
    """Mint a distinct tenant-aliased login (local+slug@domain) and bind it to cur_row — the mig-088
    isolated per-tenant credential. The explicit escape hatch (separate_login=True) AND the fresh
    login the DISABLE flow hands out. Returns (alias_email, auth_id, created, aliased=True, shared=False)."""
    ten = (client.schema("storeops").table("tenants").select("slug,name").eq("org_id", org_id)
           .limit(1).execute().data) or [{}]
    slug = (ten[0].get("slug") or ten[0].get("name") or "t")
    for n in range(0, 25):
        alias = _alias_email(email, slug, n)
        a_id, a_created, a_err = _create_or_link_auth(admin, alias, temp_pw)
        if not a_id:
            continue
        other = (client.schema("storeops").table("app_users").select("id").eq("auth_id", a_id)
                 .neq("org_id", org_id).limit(1).execute().data) or []
        if other:
            continue  # alias already belongs to yet another org — try the next suffix
        client.schema("storeops").table("app_users").update(
            {"email": alias, "auth_id": a_id, "must_reset_password": True}).eq("id", cur_row["id"]).execute()
        return alias, a_id, a_created, True, False
    raise HTTPException(409, "could not mint a distinct login for this tenant — try a different email")


def _provision_login(client, admin, cur_row, org_id, email, temp_pw, separate_login=False):
    """Bind an auth login to this tenant's app_users row. Returns
    (login_email, auth_id, created, aliased, shared).

    Consent-based (platform-core-11): if the email already has a login in a DIFFERENT tenant and
    separate_login is not set, raise PendingConnectionRequired — create_login records a consent
    invite; the user CONNECTs or DISABLEs on their own next sign-in. NO second login, NO alias, NO
    silent shared bind (the rejected wave-4 default). separate_login=True keeps the mig-088 alias
    escape hatch (isolated per-tenant credential). Fresh emails keep today's direct-create path."""
    email = (email or "").strip().lower()
    member_here, member_elsewhere = _email_login_state(client, email, org_id)
    decision = _provision_decision(member_here=member_here, member_elsewhere=member_elsewhere,
                                   separate_login=separate_login)
    if decision == "pending":
        raise PendingConnectionRequired()
    if decision in ("fresh", "reset"):
        # fresh → create_user mints a new account; reset → _create_or_link_auth finds the existing
        # IN-TENANT account and resets its password (the "Reset pw" button). Both bind cur_row.
        auth_id, created, err = _create_or_link_auth(admin, email, temp_pw)
        if not auth_id:
            raise HTTPException(500, f"could not create login: {err}")
        client.schema("storeops").table("app_users").update(
            {"auth_id": auth_id, "must_reset_password": True}).eq("id", cur_row["id"]).execute()
        return email, auth_id, created, False, False
    # decision == "alias": explicit separate_login escape hatch.
    return _mint_tenant_alias(client, admin, cur_row, org_id, email, temp_pw)


def _login_ready_response(email, access_code):
    """The UNIFORM create-login response. Fresh, reset AND pending all return this identical shape and
    message, so the admin cannot tell whether the email already exists in another tenant (the access
    code is a new temp password for fresh/reset, a connect token for pending — indistinguishable).
    `temp_password` is kept = access_code for back-compat with the existing Roles & Access UI."""
    return {"email": email, "access_code": access_code, "temp_password": access_code,
            "status": "login_ready", "aliased": False, "shared": False,
            "note": ("Access has been set up for this person — hand them the access code. When they "
                     "sign in: if they're new to MetricsPro they'll use it to set their password; if "
                     "they already use MetricsPro, they sign in with their existing password and "
                     "confirm connecting this company. The code confirms it's really them.")}


def _audit_auth_event(client, event, *, email=None, auth_id=None, org_id=None, actor=None, detail=None):
    """Best-effort identity-level audit into core.auth_event. NEVER raises (mig 707 may be un-run)."""
    try:
        client.schema("core").table("auth_event").insert({
            "event": event, "email": ((email or "").strip().lower() or None),
            "auth_id": auth_id, "org_id": org_id, "actor": actor, "detail": detail,
        }).execute()
    except Exception:
        pass


def _create_pending_invite(client, org_id, email, invited_by=None, role=None):
    """Record (or refresh) a pending account-link invite for (email, this tenant). Returns the connect
    token (the access code). Refreshes any prior pending invite so the newest code wins. Raises 500
    only if the invite table is unavailable (mig 707 un-run) — so create-login degrades LOUDLY there
    rather than silently binding a shared membership (the rejected behaviour)."""
    email = (email or "").strip().lower()
    code = "Cx" + secrets.token_urlsafe(9)
    now = datetime.now(timezone.utc)
    row = {"org_id": org_id, "email": email, "connect_token": code,
           "invited_by": (invited_by or None), "role": role, "status": "pending",
           "created_at": now.isoformat(), "expires_at": (now + timedelta(days=30)).isoformat()}
    try:
        client.schema("core").table("account_link_invite").update({"status": "revoked"}) \
            .eq("org_id", org_id).eq("email", email).eq("status", "pending").execute()
        client.schema("core").table("account_link_invite").insert(row).execute()
    except Exception as e:
        # Loud degrade, but MASKED: the client gets a generic ref (item 5b); the real cause (e.g. mig 707
        # un-run) lands in failure_log so an admin can diagnose without exposing internals.
        raise _masked_500(client, org_id, "core.create_pending_invite (mig 707?)", e)
    _audit_auth_event(client, "invite_created", email=email, org_id=org_id,
                      actor=(f"admin:{invited_by}" if invited_by else "admin"), detail={"role": role})
    return code


def _find_pending_invite(client, email, org_id, code=None):
    """The live (pending, unexpired) invite for (email, org), optionally requiring a matching code."""
    email = (email or "").strip().lower()
    try:
        rows = (client.schema("core").table("account_link_invite").select("*")
                .eq("email", email).eq("org_id", org_id).eq("status", "pending").execute().data) or []
    except Exception:
        return None
    now = datetime.now(timezone.utc)
    for r in rows:
        exp = r.get("expires_at")
        try:
            if exp and datetime.fromisoformat(str(exp).replace("Z", "+00:00")) < now:
                continue
        except Exception:
            pass
        if code is not None and (r.get("connect_token") or "") != code:
            continue
        return r
    return None


def _resolve_invite(client, inv, status, auth_id):
    try:
        client.schema("core").table("account_link_invite").update(
            {"status": status, "resolved_auth_id": auth_id,
             "resolved_at": datetime.now(timezone.utc).isoformat()}).eq("id", inv["id"]).execute()
    except Exception:
        pass


def _email_for_uid(client, uid):
    """The authenticated login's own email — from its app_users row, else the Supabase auth account."""
    rows = (client.schema("storeops").table("app_users").select("email")
            .eq("auth_id", uid).limit(1).execute().data) or []
    if rows and rows[0].get("email"):
        return (rows[0]["email"] or "").strip().lower()
    try:
        resp = get_supabase_admin().auth.admin.get_user_by_id(uid)
        user = getattr(resp, "user", None) or resp
        em = getattr(user, "email", None) or (user.get("email") if isinstance(user, dict) else None)
        return (em or "").strip().lower() or None
    except Exception:
        return None


def _set_auth_ban(admin, auth_id, banned):
    """Ban (disable) or un-ban a Supabase Auth account. DISABLE semantics: a banned account's token
    stops verifying, so the tenant middleware rejects it (its _resolve_identity fails → 401) —
    enforcement at the auth boundary the middleware already runs, so NO tenant_middleware.py change is
    needed. Returns True on success. ban_duration '876000h' ≈ 100y (indefinite); 'none' un-bans."""
    try:
        admin.auth.admin.update_user_by_id(auth_id, {"ban_duration": ("876000h" if banned else "none")})
        return True
    except Exception:
        return False


async def _deliver_access_code(client, org_id, email, code, *, record_on_invite=False):
    """EMAIL the access/invite code to the invitee via the notify Resend path (item 1a). NEVER raises —
    a send failure degrades to a visible {'delivery_status':'failed'} state, so create-login/invite
    creation still succeeds. When record_on_invite, persists the outcome + resend accounting onto the
    pending invite row (mig 712 columns; best-effort)."""
    tname = None
    try:
        t = _tenant_row(client, org_id) or {}
        tname = t.get("name")
    except Exception:
        tname = None
    ok, channel, err = await _anotify.send_invite_email(email, code, tname or "MetricsPro")
    status = "sent" if ok else "failed"
    if record_on_invite:
        try:
            client.schema("core").table("account_link_invite").update({
                "delivery_channel": channel, "delivery_status": status,
                "delivery_error": err, "delivered_at": (datetime.now(timezone.utc).isoformat() if ok else None),
                "last_sent_at": datetime.now(timezone.utc).isoformat(),
            }).eq("org_id", org_id).eq("email", email).eq("status", "pending").execute()
        except Exception:
            pass  # mig 712 un-run → outcome still returned in the response + audited below
    _audit_auth_event(client, ("invite_sent" if ok else "invite_send_failed"),
                      email=email, org_id=org_id, actor="system",
                      detail={"channel": channel, "status": status, "error": err})
    return {"delivery_status": status, "delivery_channel": channel, "delivery_error": err}


@router.post("/users/create-login")
async def create_login(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                       x_active_org: str = Header(default="")):
    """Create (or relink) the Supabase Auth account for ONE assigned user and store auth_id. Returns
    an ACCESS CODE to hand out (user resets on first login). Consent-based (platform-core-11): if the
    email ALREADY has a MetricsPro login in another tenant, NO second login / alias / shared bind is
    minted — a pending account-link invite is recorded and the response is BYTE-IDENTICAL to a
    brand-new email (the admin can't learn the email exists elsewhere). The user then CONNECTs or
    DISABLEs on their own next sign-in. Pass {separate_login:true} to force a distinct tenant-aliased
    login (the isolated-per-tenant escape hatch, e.g. kiosk clock-punching reps)."""
    _require_setting(authorization, x_active_org, "security")
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    client = sb()
    cur = client.schema("storeops").table("app_users").select("*").eq("org_id", org_id) \
        .eq("email", email).limit(1).execute().data or []
    if not cur:
        raise HTTPException(400, "assign a role to this email first (/users/assign)")
    admin = get_supabase_admin()
    chosen_pw = (body.get("temp_password") or "").strip()
    if chosen_pw:
        validate_password(client, org_id, chosen_pw)   # an admin-chosen temp pw must pass tenant policy
    temp_pw = chosen_pw or _gen_temp_pw(client, org_id)
    separate_login = bool(body.get("separate_login"))
    try:
        login_email, auth_id, created, aliased, shared = _provision_login(
            client, admin, cur[0], org_id, email, temp_pw, separate_login=separate_login)
    except PendingConnectionRequired:
        # Email already has a login elsewhere → record a consent invite; the access code IS the
        # connect token. Same shape/note as the fresh path below ⇒ no enumeration signal.
        code = _create_pending_invite(client, org_id, email, invited_by=body.get("invited_by"),
                                      role=cur[0].get("role"))
        delivery = await _deliver_access_code(client, org_id, email, code, record_on_invite=True)
        return {**_login_ready_response(email, code), **delivery}
    if aliased:
        # Reached ONLY via explicit {separate_login:true}, so it carries no enumeration signal — the
        # admin already asked for a separate login. Uniform shape + an honest alias note. (Alias reaches
        # the same inbox, so we email the code to the base address.)
        delivery = await _deliver_access_code(client, org_id, email, temp_pw)
        return {**_login_ready_response(login_email, temp_pw), "aliased": True, **delivery,
                "note": (f"A separate, isolated login “{login_email}” was created for this tenant "
                         f"(it reaches the same inbox). Hand out the access code below.")}
    delivery = await _deliver_access_code(client, org_id, email, temp_pw)
    return {**_login_ready_response(email, temp_pw), **delivery}


# ── Account linking — the user resolves a pending invite on their own next sign-in ────────────────
@router.get("/pending-connections")
def pending_connections(authorization: str = Header(default="")):
    """Pending account-link invites addressed to the AUTHENTICATED caller's OWN email. Returns ONLY
    the inviting tenant's name per invite (zero cross-tenant disclosure — never the caller's other
    tenants, never who else an email belongs to). Empty for everyone without an invite, so the vast
    majority of logins never see this. Drives the post-login connect/disable prompt."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    return _pending_connections_payload(sb(), uid)


def _pending_connections_payload(client, uid, email=None):
    """Body of GET /pending-connections, shared with /bootstrap (ONE source — never duplicate).
    `email` lets bootstrap pass the login's email straight off its already-fetched membership rows
    (normalized identically to _email_for_uid); when absent the lookup runs exactly as before."""
    email = (email or "").strip().lower() or _email_for_uid(client, uid)
    if not email:
        return {"pending": []}
    try:
        rows = (client.schema("core").table("account_link_invite").select("*")
                .eq("email", email).eq("status", "pending").execute().data) or []
    except Exception:
        return {"pending": []}   # mig 707 un-run → no invites surfaced
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        exp = r.get("expires_at")
        try:
            if exp and datetime.fromisoformat(str(exp).replace("Z", "+00:00")) < now:
                continue
        except Exception:
            pass
        org = r.get("org_id")
        tname = "a company"
        try:
            t = _tenant_row(client, org)
            if t and t.get("name"):
                tname = t["name"]
        except Exception:
            pass
        out.append({"org_id": org, "tenant_name": tname, "invited_at": r.get("created_at")})
    return {"pending": out}


@router.post("/connect-tenant")
async def connect_tenant(body: dict, authorization: str = Header(default="")):
    """The authenticated user ACCEPTS a pending invite: attach the inviting tenant as a membership on
    their EXISTING login (mig 706 shared model → the top-bar tenant switcher then applies). Requires
    the access code the admin gave them (consent). Idempotent. org_id comes from the BODY (the invite
    target), NOT a query param — the tenant middleware rewrites a query-param org_id to the caller's
    default tenant, which would clobber the target; the body value is validated against the invite."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    target_org = (body.get("org_id") or "").strip()
    code = (body.get("code") or body.get("connect_token") or "").strip()
    if not target_org or not code:
        raise HTTPException(400, "org_id and access code required")
    client = sb()
    email = _email_for_uid(client, uid)
    if not email:
        raise HTTPException(400, "no email on this login")
    inv = _find_pending_invite(client, email, target_org, code)
    if not inv:
        raise HTTPException(403, "that access code doesn't match a pending invitation for your account")
    existing = (client.schema("storeops").table("app_users").select("id,auth_id")
                .eq("org_id", target_org).eq("email", email).limit(1).execute().data) or []
    if not existing:
        raise HTTPException(409, "the invitation's account is no longer set up — ask the admin to re-add you")
    row = existing[0]
    if row.get("auth_id") == uid:
        _resolve_invite(client, inv, "accepted", uid)   # idempotent: already connected
        return {"ok": True, "connected": True, "already": True, "org_id": target_org}
    if row.get("auth_id"):
        raise HTTPException(409, "this tenant already has a login for that email")
    try:
        client.schema("storeops").table("app_users").update(
            {"auth_id": uid, "must_reset_password": False}).eq("id", row["id"]).execute()
    except Exception as e:
        raise _masked_500(client, target_org, "core.connect_tenant (mig 706?)", e)
    _resolve_invite(client, inv, "accepted", uid)
    _audit_auth_event(client, "connect", email=email, auth_id=uid, org_id=target_org, actor=f"self:{email}")
    return {"ok": True, "connected": True, "org_id": target_org}


@router.post("/disable-and-switch")
async def disable_and_switch(body: dict, authorization: str = Header(default="")):
    """The authenticated user chooses to DISABLE their existing/old login and take a FRESH, isolated
    login for the inviting tenant instead (the stale-old-account case). Requires the access code.
    Mints the new tenant-aliased login FIRST, then bans the old auth account (Supabase ban → its token
    stops verifying, enforced at the auth boundary the middleware already runs) + marks its app_users
    rows inactive. The disabled login can be reinstated ONLY by a super-admin (policy text returned)."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    target_org = (body.get("org_id") or "").strip()
    code = (body.get("code") or body.get("connect_token") or "").strip()
    if not target_org or not code:
        raise HTTPException(400, "org_id and access code required")
    client = sb()
    admin = get_supabase_admin()
    email = _email_for_uid(client, uid)
    if not email:
        raise HTTPException(400, "no email on this login")
    inv = _find_pending_invite(client, email, target_org, code)
    if not inv:
        raise HTTPException(403, "that access code doesn't match a pending invitation for your account")
    target_row = (client.schema("storeops").table("app_users").select("*")
                  .eq("org_id", target_org).eq("email", email).limit(1).execute().data) or []
    if not target_row:
        raise HTTPException(409, "the invitation's account is no longer set up — ask the admin to re-add you")
    # 1) mint the fresh isolated login for the inviting tenant FIRST (email X is held by the old acct
    #    → a tenant alias). If this fails we abort BEFORE disabling anything.
    new_pw = _gen_temp_pw()
    new_email, new_auth, created, aliased, shared = _mint_tenant_alias(
        client, admin, target_row[0], target_org, email, new_pw)
    # 2) disable the OLD login everywhere it is bound.
    _set_auth_ban(admin, uid, True)
    try:
        client.schema("storeops").table("app_users").update({"is_active": False}) \
            .eq("auth_id", uid).execute()
    except Exception:
        pass
    _resolve_invite(client, inv, "disabled_switch", new_auth)
    _audit_auth_event(client, "disable_switch", email=email, auth_id=uid, org_id=target_org,
                      actor=f"self:{email}", detail={"new_login": new_email, "new_auth_id": new_auth})
    return {"ok": True, "disabled": True, "new_login_email": new_email,
            "access_code": new_pw, "temp_password": new_pw,
            "policy": ("Your previous login has been disabled. For security, only a MetricsPro "
                       "super-admin can reinstate it — email support@metricspro.tech or open a "
                       "helpdesk ticket. Sign in with your new login and the access code above.")}


@router.post("/reinstate-login")
async def reinstate_login(body: dict, authorization: str = Header(default="")):
    """Super-admin ONLY: reinstate a login disabled via disable-and-switch (un-ban the auth account +
    re-activate its app_users rows). This is the sole reinstatement path — no tenant-admin or
    self-service (policy). Identify the login by {email} (its real email) or {auth_id}."""
    caller = _require_super_admin(authorization)
    email = (body.get("email") or "").strip().lower()
    auth_id = (body.get("auth_id") or "").strip()
    client = sb()
    admin = get_supabase_admin()
    if not auth_id:
        if not email:
            raise HTTPException(400, "email or auth_id required")
        rows = (client.schema("storeops").table("app_users").select("auth_id")
                .eq("email", email).limit(1).execute().data) or []
        auth_id = (rows[0].get("auth_id") if rows else None) or _find_auth_user_by_email(admin, email)
    if not auth_id:
        raise HTTPException(404, "no login found for that email")
    unbanned = _set_auth_ban(admin, auth_id, False)
    try:
        client.schema("storeops").table("app_users").update({"is_active": True}) \
            .eq("auth_id", auth_id).execute()
    except Exception:
        pass
    _audit_auth_event(client, "reinstate", email=(email or None), auth_id=auth_id,
                      actor=f"super_admin:{(caller or {}).get('email') or ''}")
    return {"ok": True, "reinstated": True, "auth_id": auth_id, "unbanned": unbanned}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# AUTH HARDENING — endpoints (password policy · self-serve reset · admin-set pw · invite resend/reveal
# · two-factor). Every OTP/2FA event audits via _audit_auth_event; failures degrade to generic states.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

# ── Password policy (RULE TWO: config + admin UI) ──────────────────────────────────────────────────
@router.get("/password-policy/public")
def password_policy_public():
    """PUBLIC: the owner DEFAULT password policy — drives client-side strength hints on UNAUTHENTICATED
    screens (signup / self-serve reset). The tenant's REAL policy (server-enforced) rides on /core/me;
    exposing only the default here avoids any per-email enumeration."""
    return {"policy": dict(_sec.DEFAULT_PASSWORD_POLICY), "hard_max": _sec.HARD_MAX_PASSWORD,
            "special_chars": _sec.SPECIAL_CHARS}


def _normalize_twofa_policy(raw):
    # `default_cc` (OWNER 2026-07-17) is an ADDITIVE key on the existing twofa_policy JSONB (no migration):
    # the tenant's default phone country code, applied to bare 10-digit phone entries. '+1' when
    # absent/invalid. It rides in this dict purely because twofa_policy already exists per-tenant.
    p = {"mode": "off", "channels": ["email"], "required_roles": [],
         "default_cc": _sec.DEFAULT_COUNTRY_CODE}
    if isinstance(raw, dict):
        m = str(raw.get("mode") or "off").lower()
        p["mode"] = m if m in ("off", "optional", "required") else "off"
        ch = raw.get("channels")
        if isinstance(ch, list):
            p["channels"] = [c for c in ch if c in ("email", "whatsapp", "sms")] or ["email"]
        rr = raw.get("required_roles")
        if isinstance(rr, list):
            p["required_roles"] = [str(r) for r in rr if str(r).strip()]
        if "default_cc" in raw:
            p["default_cc"] = _sec.normalize_cc(raw.get("default_cc"))
    return p


def _load_twofa_policy(client, org_id, t=_TENANT_UNFETCHED):
    """`t` lets the login hot path pass its already-fetched tenants row (None = known absent)."""
    try:
        if t is _TENANT_UNFETCHED:
            t = _tenant_row(client, org_id)
        return _normalize_twofa_policy((t or {}).get("twofa_policy"))
    except Exception:
        return _normalize_twofa_policy(None)


def _load_default_cc(client, org_id):
    """The tenant's default phone country code ('+1' fallback). Reads twofa_policy.default_cc (additive
    JSON key). Best-effort — un-run/absent config degrades to '+1'. Never raises."""
    try:
        return _sec.normalize_cc(_load_twofa_policy(client, org_id).get("default_cc"))
    except Exception:
        return _sec.DEFAULT_COUNTRY_CODE


def _twofa_required_for(policy, role, user_opted_in):
    """Does THIS user need 2FA? off→never; required→yes (all, or only required_roles); optional→only if
    the user opted in (twofa_enabled)."""
    mode = policy.get("mode")
    if mode == "off":
        return False
    if mode == "required":
        rr = policy.get("required_roles") or []
        return (not rr) or ((role or "") in rr)
    return bool(user_opted_in)   # optional


def _enforce_self_2fa(client, org_id, uid, role, twofa_enabled, x_2fa_token):
    """Guard the sensitive SELF-service 2FA endpoints (/me/2fa/settings, /me/phone, /me/phone/verify)
    that live UNDER the marker-exempt /core/me prefix, so the middleware 2FA gate never runs on them.
    When 2FA is CURRENTLY required for this user (mode 'required', or 'optional' with them opted-in), a
    valid x-2fa-token is required — else a password-only attacker could disable 2FA or swap in their own
    phone (channel bypass). BOOTSTRAP-SAFE: a user not yet required (optional-not-opted-in / policy off)
    passes through, and the email start/verify path — which mints the FIRST marker — never calls this."""
    policy = _load_twofa_policy(client, org_id)
    if _twofa_required_for(policy, role, twofa_enabled):
        if not _sec.twofa_token_valid_for(x_2fa_token, uid, org_id, _sec.now_ts()):
            raise HTTPException(401, detail={"message": "Two-factor verification required.", "code": "2fa_required"})


@router.get("/security-settings")
def get_security_settings(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The tenant's password + 2FA policy (admin Security Settings UI). Any signed-in user may READ (so
    pages can show the policy); only a 'security'-setting editor may WRITE."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    caller = _resolve_caller(client, uid, x_active_org)
    if not caller:
        raise HTTPException(403, "no tenant for this login")
    org_id = caller["org_id"]
    return {"org_id": org_id,
            "password_policy": _load_password_policy(client, org_id),
            "default_password_policy": dict(_sec.DEFAULT_PASSWORD_POLICY),
            "hard_max": _sec.HARD_MAX_PASSWORD,
            "twofa_policy": _load_twofa_policy(client, org_id),
            "channels_status": _anotify.channels_status(),
            "can_edit": _can_edit_setting(caller, "security")}


@router.put("/security-settings")
def put_security_settings(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Update the tenant password policy and/or 2FA policy. Gated on the 'security' setting permission.
    Values are normalized + clamped (max_length <= 128 hard cap) before persisting."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    caller = _resolve_caller(client, uid, x_active_org)
    if not _can_edit_setting(caller, "security"):
        raise HTTPException(403, "you don't have permission to edit Security settings")
    org_id = caller["org_id"]
    upd = {}
    if "password_policy" in body:
        upd["password_policy"] = _sec.normalize_policy(body.get("password_policy"))
    if "twofa_policy" in body:
        upd["twofa_policy"] = _normalize_twofa_policy(body.get("twofa_policy"))
    if not upd:
        raise HTTPException(400, "nothing to update")
    try:
        client.schema("storeops").table("tenants").update(upd).eq("org_id", org_id).execute()
    except Exception:
        raise HTTPException(500, "Could not save — the Security settings migration (709/711) may not be applied yet.")
    _audit_auth_event(client, "security_settings_updated", org_id=org_id,
                      actor=f"admin:{caller.get('role')}", detail={"keys": list(upd.keys())})
    return {"ok": True, **upd}


# ── Authenticated self password change (reroutes the client-side supabase-js set → policy enforced) ──
@router.post("/me/set-password")
def set_own_password(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The signed-in user sets their OWN password (must-reset first-login screen + normal change). Routed
    through the backend so the tenant password policy CANNOT be bypassed client-side (the old screen
    called supabase.auth.updateUser directly). Validates → sets via the admin API → clears must_reset."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    caller = _resolve_caller(client, uid, x_active_org)
    org_id = (caller or {}).get("org_id") or ORG_ID
    pw = body.get("new_password") or body.get("password") or ""
    validate_password(client, org_id, pw)
    try:
        get_supabase_admin().auth.admin.update_user_by_id(uid, {"password": pw})
    except Exception:
        raise HTTPException(400, "Could not update the password. Please try again.")
    try:
        client.schema("storeops").table("app_users").update(
            {"must_reset_password": False}).eq("auth_id", uid).execute()
    except Exception:
        pass
    _audit_auth_event(client, "password_set", auth_id=uid, org_id=org_id, actor="self")
    return {"ok": True}


# ── Self-serve password reset (PUBLIC — allowlisted in tenant_middleware) ────────────────────────────
def _otp_store_ok(client):
    """A harmless probe: is core.auth_otp reachable? Used to return a UNIFORM 503 (independent of whether
    an account exists) when mig 710 is un-run — so the un-run state is not an enumeration oracle."""
    try:
        client.schema("core").table("auth_otp").select("id").limit(1).execute()
        return True
    except Exception:
        return False


_RESET_GENERIC = {"ok": True, "message": "If this email has an account, a reset code has been sent."}
_UNAVAIL = {"ok": False, "message": "Password reset is temporarily unavailable. Please try again shortly."}


@router.post("/auth/forgot-password")
async def forgot_password(body: dict, request: Request, background: BackgroundTasks):
    """PUBLIC: request a reset code. ANTI-ENUMERATION — the response is ALWAYS the same generic message
    (and same code path/timing) whether or not the account exists; existence, tenant membership and
    disabled status are NEVER revealed. Sends over email (+ verified WhatsApp phone if on file). The
    email send is dispatched AFTER the response (BackgroundTasks) so the network round-trip isn't an
    inline timing delta for existing accounts."""
    email = (body.get("email") or "").strip().lower()
    client = sb()
    if not _otp_store_ok(client):
        return _UNAVAIL   # uniform for ALL emails (global infra state, not per-email) → no oracle
    if not email or "@" not in email:
        return _RESET_GENERIC
    # equalize work regardless of existence (a constant hash op either way)
    _ = _sec.hash_otp("000000", email)
    try:
        rows = (client.schema("storeops").table("app_users")
                .select("org_id,auth_id,phone,phone_verified").eq("email", email).execute().data) or []
    except Exception:
        rows = []
    live = [r for r in rows if r.get("auth_id")]
    if not live:
        return _RESET_GENERIC   # no account → say the same thing, send nothing
    org_id = live[0].get("org_id")
    phone = next((r.get("phone") for r in live if r.get("phone_verified") and r.get("phone")), None)
    channels = ["email"] + (["whatsapp"] if phone else [])
    try:
        code = _issue_otp(client, email=email, purpose="reset", channel="email", org_id=org_id,
                          dest=_sec.mask_email(email), ip=_client_ip(request))
    except OtpUnavailable:
        return _UNAVAIL
    except HTTPException as he:
        if he.status_code == 429:
            return _RESET_GENERIC   # rate-limited → still generic (never reveal via a 429)
        raise
    # Dispatch the actual send AFTER the response returns → equalizes the inline timing between an
    # existing account (which sends) and a non-existent one (which returns early). A residual, smaller
    # delta remains from the in-request OTP insert; acceptable (documented in the handoff).
    background.add_task(_anotify.send_reset_otp, email, code, channels, phone or "")
    _audit_auth_event(client, "reset_requested", email=email, org_id=org_id, actor="self")
    return _RESET_GENERIC


@router.post("/auth/reset-password")
def reset_password_otp(body: dict):
    """PUBLIC: complete a reset with {email, code, new_password}. Uniform 'Invalid or expired code.' for
    every failure mode (missing/expired/attempts/wrong) so nothing is revealed. The new password is
    validated against the tenant policy ONLY AFTER a valid code is proven (so policy detail is never a
    pre-code oracle), and the code is consumed ONLY once the password also passes (good UX on a weak pw)."""
    email = (body.get("email") or "").strip().lower()
    code = (body.get("code") or "").strip()
    new_pw = body.get("new_password") or body.get("password") or ""
    if not email or not code or not new_pw:
        raise HTTPException(400, "email, code and a new password are required")
    client = sb()
    if not _otp_store_ok(client):
        raise HTTPException(503, "Password reset is temporarily unavailable. Please try again shortly.")
    # newest live reset OTP for this email
    try:
        otp_rows = (client.schema("core").table("auth_otp").select("*")
                    .eq("email", email).eq("purpose", "reset").is_("consumed_at", "null")
                    .order("created_at", desc=True).limit(1).execute().data) or []
    except Exception:
        raise HTTPException(503, "Password reset is temporarily unavailable. Please try again shortly.")
    row = otp_rows[0] if otp_rows else None
    ok, reason = _sec.otp_verify_decision(row, code, email, _sec.now_ts(), max_attempts=OTP_MAX_ATTEMPTS)
    if not ok:
        if reason == "mismatch" and row:
            try:
                client.schema("core").table("auth_otp").update(
                    {"attempts": int(row.get("attempts") or 0) + 1}).eq("id", row["id"]).execute()
            except Exception:
                pass
        raise HTTPException(400, "Invalid or expired code.")
    # valid code proven → now find the account + enforce the tenant policy on the new password.
    try:
        au = (client.schema("storeops").table("app_users").select("org_id,auth_id")
              .eq("email", email).execute().data) or []
    except Exception:
        au = []
    live = [r for r in au if r.get("auth_id")]
    if not live:
        raise HTTPException(400, "Invalid or expired code.")
    org_id = live[0].get("org_id")
    auth_id = live[0].get("auth_id")
    validate_password(client, org_id, new_pw)   # a policy error here does NOT consume the code (retry-friendly)
    try:
        get_supabase_admin().auth.admin.update_user_by_id(auth_id, {"password": new_pw})
    except Exception:
        raise HTTPException(400, "Could not update the password. Please try again.")
    # consume the code + clear the reset flag
    try:
        client.schema("core").table("auth_otp").update(
            {"consumed_at": datetime.now(timezone.utc).isoformat()}).eq("id", row["id"]).execute()
    except Exception:
        pass
    try:
        client.schema("storeops").table("app_users").update(
            {"must_reset_password": False}).eq("email", email).execute()
    except Exception:
        pass
    _audit_auth_event(client, "password_reset", email=email, auth_id=auth_id, org_id=org_id, actor="self")
    return {"ok": True, "message": "Your password has been updated. You can now sign in."}


# ── Invite RESEND + code REVEAL + admin-assigned password ────────────────────────────────────────────
def _resend_rate_ok(client, email):
    """Server-side resend throttle: <=5 invite sends per email per hour. Best-effort (unavailable → allow
    rather than block the admin)."""
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        rows = (client.schema("core").table("auth_event").select("id")
                .eq("email", (email or "").strip().lower())
                .in_("event", ["invite_sent", "invite_send_failed"])
                .gte("created_at", since).execute().data) or []
        return len(rows) < 5
    except Exception:
        return True


@router.post("/users/resend-invite")
async def resend_invite(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                        x_active_org: str = Header(default="")):
    """Admin: RESEND the access/invite code for an assigned email in THIS tenant (newest-wins). Refreshes
    a pending account-link invite (new code + expiry) OR re-issues a temp password for a fresh/reset
    login, then re-emails it. Rate-limited (5/hr per email). Anti-enumeration: uniform response shape."""
    uid = _uid_from_token(authorization)
    caller = _resolve_caller(sb(), uid, x_active_org) if uid else None
    if not caller or not (caller.get("super_admin") or (caller.get("role") or "").lower() == "admin"
                          or _can_edit_setting(caller, "security")):
        raise HTTPException(403, "admin only")
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    client = sb()
    if not _resend_rate_ok(client, email):
        raise HTTPException(429, "Too many resends for this email in the last hour — please wait.")
    cur = (client.schema("storeops").table("app_users").select("*").eq("org_id", org_id)
           .eq("email", email).limit(1).execute().data) or []
    if not cur:
        raise HTTPException(400, "assign a role to this email first")
    # Footgun guard: a resend that falls into the in-tenant 'reset' path RESETS an active login's
    # password. Refuse when the login is already ACTIVE (bound + has signed in — the SAME logic the
    # roster uses for login_status='active') UNLESS a pending cross-tenant invite exists (the legit
    # resend target). Invited-but-never-signed-in logins (auth_id set, no last_login) still resend.
    pending = _find_pending_invite(client, email, org_id)
    if not pending and cur[0].get("auth_id") and cur[0].get("last_login"):
        raise HTTPException(400, "This login is already active — use Reset pw or Set password to change their credentials.")
    admin = get_supabase_admin()
    temp_pw = _gen_temp_pw(client, org_id)
    try:
        login_email, auth_id, created, aliased, shared = _provision_login(
            client, admin, cur[0], org_id, email, temp_pw)
        code = temp_pw
        record_on_invite = False
    except PendingConnectionRequired:
        code = _create_pending_invite(client, org_id, email,
                                      invited_by=(caller.get("role") or "admin"), role=cur[0].get("role"))
        record_on_invite = True
    # bump resend accounting on the invite row (best-effort: read-then-increment)
    if record_on_invite:
        try:
            existing = (client.schema("core").table("account_link_invite").select("id,resent_count")
                        .eq("org_id", org_id).eq("email", email).eq("status", "pending").limit(1)
                        .execute().data) or []
            if existing:
                client.schema("core").table("account_link_invite").update(
                    {"resent_count": int(existing[0].get("resent_count") or 0) + 1}).eq("id", existing[0]["id"]).execute()
        except Exception:
            pass
    delivery = await _deliver_access_code(client, org_id, email, code, record_on_invite=record_on_invite)
    _audit_auth_event(client, "invite_resent", email=email, org_id=org_id, actor=f"admin:{caller.get('role')}")
    return {"ok": True, "email": email, "access_code": code, "temp_password": code, **delivery}


@router.post("/users/reveal-code")
def reveal_code(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                      x_active_org: str = Header(default="")):
    """Reveal the CURRENT active invite/access code for troubleshooting hand-off. Allowed for a
    super-admin OR the tenant's own admin (their own tenant's data — doctrine-compatible; NEVER exposes
    which OTHER tenants an email belongs to). AUDITED (who revealed which invite, when). A fresh
    temp-password login stores no code (passwords aren't retained) → returns code_available:false with a
    hint to use Resend."""
    uid = _uid_from_token(authorization)
    caller = _resolve_caller(sb(), uid, x_active_org) if uid else None
    if not caller or not (caller.get("super_admin") or (caller.get("role") or "").lower() == "admin"
                          or _can_edit_setting(caller, "security")):
        raise HTTPException(403, "admin only")
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    client = sb()
    inv = _find_pending_invite(client, email, org_id)   # scoped to THIS tenant only (no cross-tenant peek)
    _audit_auth_event(client, "code_revealed", email=email, org_id=org_id,
                      actor=f"admin:{caller.get('role')}", detail={"found": bool(inv)})
    if not inv:
        return {"email": email, "code_available": False,
                "hint": "No stored access code for this login (temp passwords aren't retained). Use Resend to issue a new code."}
    return {"email": email, "code_available": True, "access_code": inv.get("connect_token"),
            "status": inv.get("status"), "expires_at": inv.get("expires_at"),
            "created_at": inv.get("created_at"), "last_sent_at": inv.get("last_sent_at"),
            "delivery_status": inv.get("delivery_status"), "delivery_error": inv.get("delivery_error"),
            "resent_count": inv.get("resent_count") or 0}


@router.post("/users/set-password")
async def admin_set_password(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                             x_active_org: str = Header(default="")):
    """Admin: set a SPECIFIC password for an employee in THIS tenant (generalizes create-login / Reset
    pw — one path, not a fork). Gated on the 'security' setting permission. The password must pass the
    tenant policy (server-enforced). Sets must_reset_password=True by default (toggle require_change).
    For an email that already logs in elsewhere, we CANNOT set their password (consent) → records an
    invite with the same uniform response (anti-enumeration)."""
    uid = _uid_from_token(authorization)
    caller = _resolve_caller(sb(), uid, x_active_org) if uid else None
    if not _can_edit_setting(caller, "security"):
        raise HTTPException(403, "you don't have permission to set passwords (Security setting)")
    email = (body.get("email") or "").strip().lower()
    pw = body.get("password") or ""
    require_change = bool(body.get("require_change", True))
    if not email:
        raise HTTPException(400, "email required")
    client = sb()
    validate_password(client, org_id, pw)
    cur = (client.schema("storeops").table("app_users").select("*").eq("org_id", org_id)
           .eq("email", email).limit(1).execute().data) or []
    if not cur:
        raise HTTPException(400, "assign a role to this email first (Roles & Access)")
    admin = get_supabase_admin()
    try:
        login_email, auth_id, created, aliased, shared = _provision_login(
            client, admin, cur[0], org_id, email, pw)
    except PendingConnectionRequired:
        code = _create_pending_invite(client, org_id, email,
                                      invited_by=(caller.get("role") or "admin"), role=cur[0].get("role"))
        delivery = await _deliver_access_code(client, org_id, email, code, record_on_invite=True)
        return {"ok": True, "email": email, "invited": True, **delivery,
                "note": "This person already uses MetricsPro — they were invited to connect this company (their password can't be set for them)."}
    try:
        client.schema("storeops").table("app_users").update(
            {"must_reset_password": require_change}).eq("id", cur[0]["id"]).execute()
    except Exception:
        pass
    _audit_auth_event(client, "password_admin_set", email=email, auth_id=auth_id, org_id=org_id,
                      actor=f"admin:{caller.get('role')}", detail={"require_change": require_change})
    return {"ok": True, "email": email, "require_change": require_change}


# ── Two-factor authentication ─────────────────────────────────────────────────────────────────────────
@router.get("/me/2fa")
def get_2fa_status(authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                         x_2fa_token: str = Header(default="")):
    """The signed-in user's 2FA status for the active tenant: whether it's required, whether THIS session
    is already verified (a valid x-2fa-token), the tenant's allowed channels, the user's phone state."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    u = _pick_membership(_memberships(client, uid), (x_active_org or "").strip() or None)
    if not u:
        return {"required": False, "verified": True}
    org_id = u.get("org_id") or ORG_ID
    policy = _load_twofa_policy(client, org_id)
    required = _twofa_required_for(policy, u.get("role"), u.get("twofa_enabled"))
    verified = _sec.twofa_token_valid_for(x_2fa_token, uid, org_id, _sec.now_ts())
    return {"required": required, "verified": bool(verified), "mode": policy["mode"],
            "tenant_channels": policy["channels"],
            "user_channels": u.get("twofa_channels") or ["email"],
            "phone_on_file": bool(u.get("phone")), "phone_masked": _sec.mask_phone(u.get("phone") or ""),
            "phone_verified": bool(u.get("phone_verified")),
            "channels_status": _anotify.channels_status()}


@router.post("/me/2fa/start")
async def start_2fa(body: dict, request: Request, authorization: str = Header(default=""),
                    x_active_org: str = Header(default="")):
    """Issue a 2FA OTP over the chosen channel (email always available; whatsapp only if a verified phone
    is on file). Best-effort delivery; the code row exists regardless so a channel hiccup isn't fatal."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    u = _pick_membership(_memberships(client, uid), (x_active_org or "").strip() or None)
    if not u:
        raise HTTPException(403, "no tenant for this login")
    org_id = u.get("org_id") or ORG_ID
    email = (u.get("email") or _email_for_uid(client, uid) or "").strip().lower()
    policy = _load_twofa_policy(client, org_id)
    want = (body.get("channel") or "").strip().lower()
    channel = want if want in policy["channels"] else policy["channels"][0]
    if channel == "whatsapp" and not (u.get("phone_verified") and u.get("phone")):
        channel = "email"   # no verified phone → fall back to email
    phone = u.get("phone") if channel == "whatsapp" else ""
    dest = _sec.mask_phone(phone or "") if channel == "whatsapp" else _sec.mask_email(email)
    try:
        code = _issue_otp(client, email=email, purpose="2fa", channel=channel, org_id=org_id,
                          auth_id=uid, dest=dest, ip=_client_ip(request))
    except OtpUnavailable:
        raise HTTPException(503, "Two-factor is temporarily unavailable. Please try again shortly.")
    ok, ch, err = await _anotify.send_2fa_otp(email, code, channel, phone=phone or "")
    _audit_auth_event(client, "2fa_sent", email=email, auth_id=uid, org_id=org_id,
                      actor="self", detail={"channel": ch, "ok": ok})
    return {"sent": bool(ok), "channel": ch, "masked_dest": dest,
            "message": (f"A code was sent to {dest}." if ok else
                        "We couldn't send a code on that channel — try another channel.")}


@router.post("/me/2fa/verify")
def verify_2fa(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Verify a 2FA code and mint a signed 'verified session' marker (x-2fa-token) the client sends on
    every request. 'remember' → a 30-day device marker; otherwise a 12-hour session marker. Uniform
    'Invalid or expired code.' on any failure."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    u = _pick_membership(_memberships(client, uid), (x_active_org or "").strip() or None)
    if not u:
        raise HTTPException(403, "no tenant for this login")
    org_id = u.get("org_id") or ORG_ID
    email = (u.get("email") or _email_for_uid(client, uid) or "").strip().lower()
    code = (body.get("code") or "").strip()
    ok, reason = _verify_otp(client, email=email, purpose="2fa", code=code)
    if not ok:
        if reason == "unavailable":
            raise HTTPException(503, "Two-factor is temporarily unavailable. Please try again shortly.")
        raise HTTPException(400, "Invalid or expired code.")
    remember = bool(body.get("remember"))
    device = (body.get("device_id") or "").strip() or secrets.token_urlsafe(9)
    ttl = (30 * 86400) if remember else (12 * 3600)
    exp = _sec.now_ts() + ttl
    token = _sec.mint_2fa_token(uid, org_id, device, exp)
    try:
        client.schema("core").table("twofa_device").insert({
            "auth_id": uid, "org_id": org_id, "device_id": device,
            "label": (body.get("label") or None),
            "expires_at": datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()}).execute()
    except Exception:
        pass  # mig 711 un-run → the stateless marker still works; the device audit row is best-effort
    _audit_auth_event(client, "2fa_verified", email=email, auth_id=uid, org_id=org_id,
                      actor="self", detail={"remember": remember})
    return {"ok": True, "token": token, "device_id": device, "remember": remember,
            "expires_at": datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()}


@router.post("/me/2fa/settings")
def set_2fa_settings(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                          x_2fa_token: str = Header(default="")):
    """The user turns 2FA on/off for themselves (matters under the 'optional' tenant mode) and picks
    channels. Cannot turn OFF when the tenant policy is 'required'. When 2FA is currently required for
    this user, a valid 2FA marker is required to change these settings (prevents a password-only
    attacker from disabling it)."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    u = _pick_membership(_memberships(client, uid), (x_active_org or "").strip() or None)
    if not u:
        raise HTTPException(403, "no tenant for this login")
    org_id = u.get("org_id") or ORG_ID
    _enforce_self_2fa(client, org_id, uid, u.get("role"), u.get("twofa_enabled"), x_2fa_token)
    policy = _load_twofa_policy(client, org_id)
    enabled = bool(body.get("enabled"))
    if policy["mode"] == "required":
        enabled = True   # can't self-disable a required policy
    channels = [c for c in (body.get("channels") or []) if c in ("email", "whatsapp")] or ["email"]
    try:
        client.schema("storeops").table("app_users").update(
            {"twofa_enabled": enabled, "twofa_channels": channels}).eq("id", u["id"]).execute()
    except Exception:
        raise HTTPException(500, "Could not save — the 2FA migration (711) may not be applied yet.")
    _audit_auth_event(client, "2fa_settings", auth_id=uid, org_id=org_id, actor="self",
                      detail={"enabled": enabled, "channels": channels})
    return {"ok": True, "enabled": enabled, "channels": channels}


@router.post("/me/phone")
async def set_phone(body: dict, request: Request, authorization: str = Header(default=""),
                    x_active_org: str = Header(default=""), x_2fa_token: str = Header(default="")):
    """Set/replace the user's phone (unverified) and send a WhatsApp verification code. The phone becomes
    a usable 2FA channel only AFTER it's verified. When 2FA is currently required for this user, a valid
    2FA marker is required — else a password-only attacker could swap in their own number and receive the
    2FA codes (channel bypass)."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    u = _pick_membership(_memberships(client, uid), (x_active_org or "").strip() or None)
    if not u:
        raise HTTPException(403, "no tenant for this login")
    org_id = u.get("org_id") or ORG_ID
    _enforce_self_2fa(client, org_id, uid, u.get("role"), u.get("twofa_enabled"), x_2fa_token)
    email = (u.get("email") or _email_for_uid(client, uid) or "").strip().lower()
    # Auto-correct the country code: a bare 10-digit → tenant default_cc (+1) + digits; an already-CC'd
    # or international number is kept verbatim. Un-normalizable → clear 400 (never silently mangled).
    phone, perr = _sec.normalize_phone(body.get("phone") or "", _load_default_cc(client, org_id))
    if perr or not phone:
        raise HTTPException(400, perr or "Enter a valid phone number (with country code).")
    try:
        client.schema("storeops").table("app_users").update(
            {"phone": phone, "phone_verified": False}).eq("id", u["id"]).execute()
    except Exception:
        raise HTTPException(500, "Could not save the phone — the 2FA migration (711) may not be applied yet.")
    try:
        code = _issue_otp(client, email=email, purpose="phone_verify", channel="whatsapp", org_id=org_id,
                          auth_id=uid, dest=_sec.mask_phone(phone), ip=_client_ip(request))
    except OtpUnavailable:
        raise HTTPException(503, "Phone verification is temporarily unavailable. Please try again shortly.")
    ok, ch, err = await _anotify.send_phone_verify_otp(phone, code)
    _audit_auth_event(client, "phone_verify_sent", auth_id=uid, org_id=org_id, actor="self",
                      detail={"ok": ok})
    return {"sent": bool(ok), "channel": "whatsapp", "masked": _sec.mask_phone(phone),
            "message": ("A verification code was sent by WhatsApp." if ok else
                        "We couldn't send a WhatsApp code — check the number or try email 2FA instead.")}


@router.post("/me/phone/verify")
def verify_phone(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                       x_2fa_token: str = Header(default="")):
    """Verify the phone with the WhatsApp code → mark it verified + enable WhatsApp as a 2FA channel. When
    2FA is currently required for this user, a valid 2FA marker is required (same channel-bypass guard as
    /me/phone: a marker-less caller must not be able to promote an attacker-controlled number)."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    u = _pick_membership(_memberships(client, uid), (x_active_org or "").strip() or None)
    if not u:
        raise HTTPException(403, "no tenant for this login")
    org_id = u.get("org_id") or ORG_ID
    _enforce_self_2fa(client, org_id, uid, u.get("role"), u.get("twofa_enabled"), x_2fa_token)
    email = (u.get("email") or _email_for_uid(client, uid) or "").strip().lower()
    ok, reason = _verify_otp(client, email=email, purpose="phone_verify", code=(body.get("code") or "").strip())
    if not ok:
        if reason == "unavailable":
            raise HTTPException(503, "Phone verification is temporarily unavailable. Please try again shortly.")
        raise HTTPException(400, "Invalid or expired code.")
    chans = [c for c in (u.get("twofa_channels") or ["email"]) if c in ("email", "whatsapp")]
    if "whatsapp" not in chans:
        chans.append("whatsapp")
    try:
        client.schema("storeops").table("app_users").update(
            {"phone_verified": True, "twofa_channels": chans}).eq("id", u["id"]).execute()
    except Exception:
        pass
    _audit_auth_event(client, "phone_verified", auth_id=uid, org_id=org_id, actor="self")
    return {"ok": True, "verified": True}


@router.post("/users/reset-password")
def reset_password(body: dict, authorization: str = Header(default="")):
    """Super-admin: reset ANY user's password by email, ACROSS ALL TENANTS (not org-scoped, unlike
    /users/create-login). Uses the admin SDK to (re)set the Supabase Auth password and forces a change
    on next login wherever the email has an app_users row. Returns the temp password to hand out. Pass
    {email, temp_password?} — temp_password optional (auto-generated if omitted)."""
    _require_super_admin(authorization)
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    admin = get_supabase_admin()
    existing = _find_auth_user_by_email(admin, email)
    if not existing:
        raise HTTPException(404, f"no auth account exists for {email} — create their login first (Roles & Access).")
    chosen = (body.get("temp_password") or "").strip()
    if chosen:
        # Cross-tenant reset (no single org) → enforce the owner DEFAULT policy on an admin-chosen pw.
        perr = _sec.password_errors(_sec.DEFAULT_PASSWORD_POLICY, chosen)
        if perr:
            raise HTTPException(400, " ".join(perr))
    temp_pw = chosen or _gen_temp_pw()
    try:
        admin.auth.admin.update_user_by_id(existing, {"password": temp_pw})
    except Exception as e:
        raise _masked_500(sb(), ORG_ID, "core.reset_password.update_auth", e)
    # Force a reset-on-next-login flag wherever this email is provisioned (any org).
    try:
        sb().schema("storeops").table("app_users").update(
            {"must_reset_password": True}).eq("email", email).execute()
    except Exception:
        pass
    return {"ok": True, "email": email, "temp_password": temp_pw, "auth_id": existing}


@router.post("/tenants/{org_id}/reset-admin-password")
def reset_tenant_admin_password(org_id: str, body: dict = None, authorization: str = Header(default="")):
    """Super-admin: reset a TENANT's admin login password (on request from that tenant's admin who
    is locked out). Finds the tenant's admin app_users row(s) that have a provisioned login — the
    common case is exactly one, which is reset directly. If a tenant has MORE than one admin login,
    returns {needs_email, admins:[…]} so the caller re-submits with {email} to pick which. Sets a
    temp password + forces a change on next login. Returns the temp password to hand back. Body is
    optional: {email? (disambiguate), temp_password? (auto-generated if omitted)}."""
    _require_super_admin(authorization)
    body = body or {}
    client = sb()
    ten = (client.schema("storeops").table("tenants").select("org_id,name")
           .eq("org_id", org_id).limit(1).execute().data) or []
    if not ten:
        raise HTTPException(404, "no such tenant")
    admins = (client.schema("storeops").table("app_users").select("*")
              .eq("org_id", org_id).eq("role", "admin").execute().data) or []
    logins = [a for a in admins if a.get("auth_id") and a.get("email")]
    if not logins:
        raise HTTPException(404, "this tenant has no admin login yet — create the tenant admin first (＋ Add a company, or Roles & Access).")
    email = (body.get("email") or "").strip().lower()
    if email:
        target = next((a for a in logins if (a.get("email") or "").lower() == email), None)
        if not target:
            raise HTTPException(404, f"{email} is not an admin login for this tenant")
    elif len(logins) == 1:
        target = logins[0]
    else:
        # more than one admin login — let the caller choose which to reset
        return {"ok": False, "needs_email": True, "tenant": ten[0].get("name"), "org_id": org_id,
                "admins": [{"email": a.get("email"), "full_name": a.get("full_name")} for a in logins]}
    target_email = (target.get("email") or "").strip().lower()
    admin = get_supabase_admin()
    existing = _find_auth_user_by_email(admin, target_email)
    if not existing:
        raise HTTPException(404, f"no auth account exists for {target_email} — create their login first (Roles & Access).")
    chosen = (body.get("temp_password") or "").strip()
    if chosen:
        validate_password(client, org_id, chosen)   # tenant policy for this tenant's admin pw
    temp_pw = chosen or _gen_temp_pw(client, org_id)
    try:
        admin.auth.admin.update_user_by_id(existing, {"password": temp_pw})
    except Exception as e:
        raise _masked_500(sb(), ORG_ID, "core.reset_password.update_auth", e)
    # force reset-on-next-login for THIS tenant's admin row (scoped to org + email)
    try:
        client.schema("storeops").table("app_users").update({"must_reset_password": True}) \
            .eq("org_id", org_id).eq("email", target_email).execute()
    except Exception:
        pass
    return {"ok": True, "tenant": ten[0].get("name"), "org_id": org_id,
            "email": target_email, "temp_password": temp_pw, "auth_id": existing}


@router.post("/users/bulk-provision")
def bulk_provision(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                   x_active_org: str = Header(default="")):
    """Create logins for every assigned core.users row that has an email and no auth_id yet
    (optionally limited to body['emails']). Returns the per-user temp passwords to distribute."""
    _require_setting(authorization, x_active_org, "security")
    client = sb()
    rows = client.schema("storeops").table("app_users").select("*").eq("org_id", org_id) \
        .execute().data or []
    want = set((e or "").lower() for e in (body.get("emails") or []))
    admin = get_supabase_admin()
    created, skipped, results = 0, 0, []
    for u in rows:
        email = (u.get("email") or "").strip().lower()
        if not email or u.get("auth_id"):
            skipped += 1
            continue
        if want and email not in want:
            continue
        temp_pw = _gen_temp_pw(client, org_id)
        auth_id, was_new, err = _create_or_link_auth(admin, email, temp_pw)
        if not auth_id:
            results.append({"email": email, "ok": False, "error": err})
            continue
        client.schema("storeops").table("app_users").update(
            {"auth_id": auth_id, "must_reset_password": True}).eq("id", u["id"]).execute()
        created += 1
        results.append({"email": email, "ok": True, "temp_password": temp_pw,
                        "name": u.get("full_name"), "role": u.get("role")})
    return {"created": created, "skipped": skipped, "results": results}


@router.post("/users/delete")
def delete_user(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                x_active_org: str = Header(default="")):
    """Hard-delete an app user: remove the storeops.app_users row AND its Supabase Auth account."""
    _require_setting(authorization, x_active_org, "security")
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    client = sb()
    rows = client.schema("storeops").table("app_users").select("auth_id") \
        .eq("org_id", org_id).eq("email", email).limit(1).execute().data or []
    auth_id = rows[0].get("auth_id") if rows else None
    client.schema("storeops").table("app_users").delete() \
        .eq("org_id", org_id).eq("email", email).execute()
    auth_deleted = False
    if auth_id:
        try:
            get_supabase_admin().auth.admin.delete_user(auth_id)
            auth_deleted = True
        except Exception:
            pass
    return {"deleted": bool(rows), "auth_deleted": auth_deleted}


@router.post("/users/deactivate")
def deactivate_user(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                    x_active_org: str = Header(default="")):
    """Soft-disable an app user (keeps the auth account; flip is_active)."""
    _require_setting(authorization, x_active_org, "security")
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    res = sb().schema("storeops").table("app_users").update(
        {"is_active": bool(body.get("is_active", False))}) \
        .eq("org_id", org_id).eq("email", email).execute()
    return {"updated": len(res.data or [])}


def purge_app_user(org_id, *, email=None, employee_id=None, hard=True):
    """Remove (or deactivate) the login(s) for a person, matched by email AND/OR employee_id.

    Called by employee delete/deactivate from BOTH the StoreOps roster and the Roles & Access
    page, so removing an employee is reflected everywhere instead of leaving the app_users row +
    Supabase Auth account dangling as a "ghost" manual user in Roles (the reported delete-sync bug).
      hard=True  → delete the app_users row(s) + the Supabase Auth account(s).
      hard=False → flip is_active=False (keeps the auth account but blocks access).
    No-op (matched:0) if no email/employee_id or nothing matches."""
    email = (email or "").strip().lower()
    employee_id = (str(employee_id).strip() or None) if employee_id is not None else None
    if not email and not employee_id:
        return {"matched": 0}
    client = get_supabase()
    tbl = lambda: client.schema("storeops").table("app_users")
    found = {}
    try:
        if email:
            for r in (tbl().select("id,auth_id,email,employee_id")
                      .eq("org_id", org_id).eq("email", email).execute().data or []):
                found[r["id"]] = r
        if employee_id:
            for r in (tbl().select("id,auth_id,email,employee_id")
                      .eq("org_id", org_id).eq("employee_id", employee_id).execute().data or []):
                found[r["id"]] = r
    except Exception:
        return {"matched": 0}
    rows = list(found.values())
    if not rows:
        return {"matched": 0}
    if not hard:
        for r in rows:
            tbl().update({"is_active": False}).eq("id", r["id"]).execute()
        return {"matched": len(rows), "deactivated": len(rows)}
    ids = [r["id"] for r in rows]
    tbl().delete().in_("id", ids).execute()
    admin, auth_deleted = None, 0
    for r in rows:
        if r.get("auth_id"):
            try:
                if admin is None:
                    admin = get_supabase_admin()
                admin.auth.admin.delete_user(r["auth_id"])
                auth_deleted += 1
            except Exception:
                pass
    return {"matched": len(rows), "deleted": len(ids), "auth_deleted": auth_deleted}


@router.post("/employees/purge")
def purge_employee(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                   x_active_org: str = Header(default="")):
    """Delete or deactivate a person from BOTH the StoreOps roster and the Roles module in one
    call (the Roles & Access remove action). Identify by employee_pk (storeops.employees.id) for
    a real employee, or by email/employee_id for a manually-added Roles user (which has no
    employees row). mode='delete' hard-removes the employees row + login + Supabase Auth account;
    mode='deactivate' flips both is_active=False and revokes access (keeps the auth account)."""
    _require_setting(authorization, x_active_org, "security")
    mode = (body.get("mode") or "delete").strip().lower()
    hard = mode != "deactivate"
    emp_pk = body.get("employee_pk", body.get("id"))
    email = (body.get("email") or "").strip().lower()
    employee_id = body.get("employee_id")
    client = sb()
    name = None
    # Synthetic negative ids are Roles-only manual users (no employees row) — skip the roster op.
    synthetic = False
    try:
        synthetic = int(str(emp_pk)) < 0
    except (TypeError, ValueError):
        synthetic = False
    if emp_pk not in (None, "") and not synthetic:
        rows = client.schema("storeops").table("employees").select("id,name,email,employee_id") \
            .eq("org_id", org_id).eq("id", str(emp_pk)).limit(1).execute().data or []
        if rows:
            name = rows[0].get("name")
            email = email or (rows[0].get("email") or "").lower()
            employee_id = employee_id or rows[0].get("employee_id")
            if hard:
                client.schema("storeops").table("employees").delete().eq("id", str(emp_pk)).execute()
            else:
                client.schema("storeops").table("employees").update({"is_active": False}) \
                    .eq("id", str(emp_pk)).execute()
    login = purge_app_user(org_id, email=email, employee_id=employee_id, hard=hard)
    return {"ok": True, "mode": mode, "name": name, "login": login}


# ── Employee Dashboard (role-gated widgets) ───────────────────────────────────
EMP_WIDGETS = ["schedule", "timeoff", "hours", "commission", "targets",
               "report_card", "commission_tracking", "flags", "chargebacks", "phone_priority",
               "device_history"]


def _emp_period(period):
    import calendar as _cal
    if period:
        return period
    n = datetime.now(timezone.utc)
    return f"{_cal.month_name[n.month]} {n.year}"


def _emp_period_variants(period):
    """Both spellings of a month-period ('July 2026' and '2026-07') so the dashboard's exact-match
    queries hit the data no matter which way it was stored (the recurring period-spelling gotcha) —
    important now the dashboard has a month picker."""
    import calendar as _cal
    p = str(period or "").strip()
    out = {p}
    try:
        names = {m.lower(): i for i, m in enumerate(_cal.month_name) if m}
        if len(p) >= 7 and p[:4].isdigit() and p[4] == "-" and p[5:7].isdigit():
            mo, yr = int(p[5:7]), int(p[:4])
        else:
            parts = p.split()
            mo, yr = names[parts[0].lower()], int(parts[1])
        out.add(f"{_cal.month_name[mo]} {yr}")
        out.add(f"{yr}-{mo:02d}")
    except Exception:
        pass
    return [x for x in out if x]


@router.get("/employee-widgets")
def employee_widgets_keys():
    """The canonical widget list (for the roles manager toggles)."""
    return {"widgets": EMP_WIDGETS}


@router.get("/employee-dashboard")
def employee_dashboard(employee_id: str = "", period: str = "", org_id: str = ORG_ID):
    """One bundle for an employee's self-service dashboard, plus the effective widget
    visibility (role employee_widgets). Sections compute regardless; the frontend hides
    disabled widgets. employee_id identifies the rep (admins pass it; a self-scoped rep is
    pinned to their own by the frontend)."""
    from datetime import date as _date, timedelta as _td
    if not employee_id:
        raise HTTPException(400, "employee_id required")
    client = sb()
    emp = (client.schema("storeops").table("employees").select("*")
           .eq("org_id", org_id).eq("employee_id", employee_id).limit(1).execute().data or [])
    if emp:
        emp = emp[0]
    else:
        # Managers (and some leaders) exist only as app_users, with no storeops.employees row.
        # Fall back to their app_user so the portal still renders the dashboard widgets (and
        # clock-in) instead of a dead spinner; sales-derived sections just come back empty.
        au = (client.schema("storeops").table("app_users").select("full_name,store_code,employee_id")
              .eq("org_id", org_id).eq("employee_id", employee_id).limit(1).execute().data or [])
        if not au:
            raise HTTPException(404, "employee not found")
        a = au[0]
        emp = {"employee_id": employee_id, "name": a.get("full_name") or "",
               "home_store": a.get("store_code") or "", "epay_salesperson": "", "pay_rate": 0}
    name = (emp.get("name") or "").strip()
    eslp = (emp.get("epay_salesperson") or "").strip()
    period = _emp_period(period)
    pvar = _emp_period_variants(period)

    # Robust name match: the employee is "Ali" but the sales/commission data uses the full
    # "ali, mohammad khalid". Match on normalized words, not exact string — otherwise the dashboard
    # shows "No commission" while the Rep Commission Report has real numbers (the reported bug).
    import re as _re

    def _norm(s):
        return _re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()
    _emp_norms = {_norm(x) for x in (name, eslp) if _norm(x)}
    _emp_tokens = set()
    for n in _emp_norms:
        _emp_tokens.update(n.split())

    def _is_me(rec, *fields):
        for f in fields:
            rn = _norm(rec.get(f))
            if not rn:
                continue
            if rn in _emp_norms:                     # exact (normalized)
                return True
            rtok = set(rn.split())
            for en in _emp_norms:                    # full-name subset either direction
                ent = set(en.split())
                if ent and (ent <= rtok or rtok <= ent):
                    return True
            if len(_emp_tokens) == 1 and next(iter(_emp_tokens)) in rtok:  # single-name rep as a word
                return True
        return False

    # Effective widgets = role default (all-on if no role), then this employee's
    # per-person overrides applied on top (#1b).
    #
    # GUARDED (2026-07-30): this block is COSMETIC — it only decides which widgets the portal draws,
    # and every section below is computed and returned regardless (see this endpoint's docstring), so
    # the fallback exposes nothing the same response does not already carry. Un-guarded, a hiccup on
    # either of these two small reads took the rep's entire dashboard down with a 500 (the failure_log
    # RuntimeError frame at this line). Same shape as the phone_priority guard below; success-path
    # behaviour is byte-identical, and the fallback is the documented "no role ⇒ all widgets on".
    widgets = {k: True for k in EMP_WIDGETS}
    role_name = None
    try:
        au = (client.schema("storeops").table("app_users").select("role,widget_overrides")
              .eq("org_id", org_id).eq("employee_id", employee_id).limit(1).execute().data or [])
        role_name = au[0].get("role") if au else None
        if role_name:
            rr = (client.schema("storeops").table("roles").select("permissions")
                  .eq("org_id", org_id).eq("name", role_name).limit(1).execute().data or [])
            ew = (rr[0].get("permissions") or {}).get("employee_widgets") if rr else None
            if isinstance(ew, dict):
                widgets = {k: bool(ew.get(k, True)) for k in EMP_WIDGETS}
        ovr = au[0].get("widget_overrides") if au else None
        if isinstance(ovr, dict):
            for k, v in ovr.items():
                if k in widgets:
                    widgets[k] = bool(v)
    except Exception:
        widgets = {k: True for k in EMP_WIDGETS}   # partial application ⇒ reset to the plain default

    out = {
        "employee": {"employee_id": employee_id, "name": name, "store": emp.get("home_store"),
                     "epay_salesperson": eslp, "role": role_name, "pay_rate": float(emp.get("pay_rate") or 0)},
        "period": period, "widgets": widgets,
    }

    # Priority-sell list (module 095): devices at this rep's store in the final pct% of their pay
    # window. Guarded + lazy-imported so it never breaks the dashboard if the ledger isn't built yet.
    try:
        from app.modules.payables.engine import priority_for_store
        out["phone_priority"] = priority_for_store(client, org_id, emp.get("home_store"))
    except Exception:
        out["phone_priority"] = []

    # Commission (selected period) + tracking (all periods). period-variant match so the picked month
    # hits the data whether it's stored 'July 2026' or '2026-07'.
    comm = client.schema("commcalc").table("rep_commissions").select("*").eq("org_id", org_id).in_("period", pvar).execute().data or []
    myc = next((c for c in comm if _is_me(c, "storeops_name", "epay_salesperson")), None)
    out["commission"] = myc
    allc = (client.schema("commcalc").table("rep_commissions")
            .select("period,period_year,period_month,total_payout,tier,kpis_met,total_kpis,storeops_name,epay_salesperson")
            .eq("org_id", org_id).execute().data or [])
    track = [c for c in allc if _is_me(c, "storeops_name", "epay_salesperson")]
    track.sort(key=lambda r: (r.get("period_year") or 0, r.get("period_month") or 0))
    out["commission_tracking"] = track
    # The rep name used by the sales/commission data (e.g. 'ali, mohammad khalid'), so the coaching +
    # target-calendar calls (which match rep names exactly) scope to the right person, not the short
    # employee name. Prefer the selected month's row, else the most recent month the rep has.
    _src = myc or (track[-1] if track else None)
    rep_full = ((_src.get("storeops_name") or _src.get("epay_salesperson")) if _src else "") or ""
    out["employee"]["rep_name"] = rep_full.strip() or eslp or name

    # Flags + chargebacks attributed to this rep.
    fl = client.schema("commcalc").table("flags").select("*").eq("org_id", org_id).in_("period", pvar).execute().data or []
    myf = [f for f in fl if _is_me(f, "epay_salesperson")]
    out["flags"] = myf
    cbs = client.schema("commcalc").table("chargeback_items").select("*").eq("org_id", org_id).in_("period", pvar).execute().data or []
    mycb = [c for c in cbs if _is_me(c, "epay_salesperson")]
    out["chargebacks"] = mycb

    # Schedule (upcoming 7 days) + hours (for the SELECTED month) from storeops.shifts.
    today = _date.today()
    out["schedule"] = (client.schema("storeops").table("shifts").select("*")
                       .eq("org_id", org_id).eq("is_deleted", False).eq("employee_id", employee_id)
                       .gte("shift_date", today.isoformat())
                       .lte("shift_date", (today + _td(days=7)).isoformat())
                       .order("shift_date").execute().data or [])
    # Hours are for the picked month (so a prior month shows the hours it closed with), not always today's.
    import calendar as _cal2
    try:
        _names = {m.lower(): i for i, m in enumerate(_cal2.month_name) if m}
        _p = str(period).strip()
        if len(_p) >= 7 and _p[:4].isdigit() and _p[4] == "-" and _p[5:7].isdigit():
            _yr, _mo = int(_p[:4]), int(_p[5:7])
        else:
            _parts = _p.split(); _mo = _names[_parts[0].lower()]; _yr = int(_parts[1])
    except Exception:
        _yr, _mo = today.year, today.month
    ym = f"{_yr}-{_mo:02d}"
    nxt = f"{_yr + 1}-01-01" if _mo == 12 else f"{_yr}-{_mo + 1:02d}-01"
    msh = (client.schema("storeops").table("shifts").select("scheduled_hours,actual_hours")
           .eq("org_id", org_id).eq("is_deleted", False).eq("employee_id", employee_id)
           .gte("shift_date", f"{ym}-01").lt("shift_date", nxt).execute().data or [])
    rate = float(emp.get("pay_rate") or 0)
    sh = sum(float(s.get("scheduled_hours") or 0) for s in msh)
    ah = sum((float(s.get("actual_hours") or 0) or float(s.get("scheduled_hours") or 0)) for s in msh)
    out["hours"] = {"scheduled_hours": round(sh, 1), "actual_hours": round(ah, 1),
                    "pay_rate": rate, "scheduled_pay": round(sh * rate, 2),
                    "actual_pay": round(ah * rate, 2), "shifts": len(msh)}

    # Report card = full performance summary.
    out["report_card"] = {
        "tier": (myc or {}).get("tier"),
        "kpis_met": (myc or {}).get("kpis_met"),
        "total_kpis": (myc or {}).get("total_kpis"),
        "kpi_values": (myc or {}).get("kpi_values") or {},
        "commission_earned": (myc or {}).get("final_payout") if myc and myc.get("final_payout") is not None else (myc or {}).get("total_payout"),
        "flags_count": len(myf),
        "chargebacks_count": len(mycb),
        "chargebacks_total": round(sum(float(c.get("amount") or 0) for c in mycb), 2),
    }
    out["targets"] = {"acc_target": (myc or {}).get("acc_target"), "acc_comm": (myc or {}).get("acc_comm")}
    return out


@router.put("/employee-widgets")
def set_employee_widget_overrides(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                                  x_active_org: str = Header(default="")):
    """Per-employee Employee-Dashboard widget overrides (#1b). Body:
    {employee_id?, email?, widget_overrides: {widget_key: bool} | null}. Writes onto the
    person's storeops.app_users row (so they must be assigned a role first). null/{} = clear
    (inherit the role default). Unknown widget keys are dropped."""
    _require_setting(authorization, x_active_org, "security")
    eid = (body.get("employee_id") or "").strip()
    email = (body.get("email") or "").strip().lower()
    if not eid and not email:
        raise HTTPException(400, "employee_id or email required")
    raw = body.get("widget_overrides")
    if raw in (None, {}):
        ovr = None
    elif isinstance(raw, dict):
        ovr = {k: bool(v) for k, v in raw.items() if k in EMP_WIDGETS}
        ovr = ovr or None
    else:
        raise HTTPException(400, "widget_overrides must be an object or null")
    client = sb()
    q = client.schema("storeops").table("app_users").select("id").eq("org_id", org_id)
    q = q.eq("employee_id", eid) if eid else q.eq("email", email)
    rows = q.limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "no app login for this person — assign a role first")
    client.schema("storeops").table("app_users").update({"widget_overrides": ovr}) \
        .eq("id", rows[0]["id"]).execute()
    return {"ok": True, "employee_id": eid or None, "email": email or None, "widget_overrides": ovr}



# ═══════════════════════════════════════════════════════════════════════════════════════════════
# SUPPORT DOCS (mig 715) — per-page help registry with TWO views: a trimmed user-facing `user_md`
# (the "?" panel, readable by any signed-in user) and the full `support_md` playbook (support staff
# only). Docs live in `core.support_doc` (core schema, exposed). Resolution: page_key/pathname →
# longest-prefix matching PUBLISHED doc; a TENANT-org override beats the HOUSE (global) row at equal
# specificity. Author/list/import/delete are gated by the SAME cross-tenant support gate as the console
# (lazy-imported from the helpdesk router — one gate implementation, no duplication, no import cycle).
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def _support_gate(authorization, x_active_org=""):
    """True if the caller may see full support content / edit docs. Delegates to the single support
    gate defined in the helpdesk router (super_admin OR house-org membership w/ modules.support)."""
    try:
        from app.modules.helpdesk.router import _support_ctx
        return _support_ctx(authorization or "", x_active_org or "") is not None
    except Exception:
        return False


def _resolve_support_doc(docs, path, tenant_org, house_org=ORG_ID):
    """PURE longest-prefix resolver (unit-proven). From `docs` (published rows for the tenant ∪ house),
    return the row whose page_key is the longest boundary-prefix of `path`; a tenant-org row beats the
    house row at EQUAL page_key length. Returns None when nothing matches."""
    p = (str(path or "").split("?")[0].split("#")[0]).rstrip("/") or "/"
    best, best_rank = None, (-1, -1)
    for d in (docs or []):
        if not d.get("is_published", True):
            continue
        pk = (str(d.get("page_key") or "").rstrip("/")) or "/"
        matched = (p == pk) or (pk != "/" and p.startswith(pk + "/")) or (pk == "/")
        if not matched:
            continue
        is_tenant = 1 if (d.get("org_id") == tenant_org and tenant_org != house_org) else 0
        rank = (len(pk), is_tenant)
        if rank > best_rank:
            best_rank, best = rank, d
    return best


_DOC_FIELDS = ("page_key", "title", "module", "user_md", "support_md", "common_issues",
               "permissions_needed", "related_settings", "is_published")


@router.get("/support-doc/resolve")
def support_doc_resolve(path: str = "", authorization: str = Header(default=""),
                              x_active_org: str = Header(default="")):
    """Resolve the help doc for a pathname (the "?" panel calls this for the CURRENT page). Returns the
    trimmed `user_md` view for a normal user; the FULL row when the caller passes the support gate.
    FAIL-SILENT: any error → {found: false} so the panel never breaks a page. The caller's tenant (for a
    tenant override) is derived from the token; unauthenticated → house docs only."""
    client = sb()
    tenant_org = ORG_ID
    try:
        uid = _uid_from_token(authorization)
        if uid:
            caller = _resolve_caller(client, uid, x_active_org)
            if caller and caller.get("org_id"):
                tenant_org = caller["org_id"]
    except Exception:
        tenant_org = ORG_ID
    orgs = list({tenant_org, ORG_ID})
    try:
        docs = (client.schema("core").table("support_doc").select("*")
                .in_("org_id", orgs).eq("is_published", True).execute().data) or []
    except Exception:
        return {"found": False, "path": path}
    doc = _resolve_support_doc(docs, path, tenant_org)
    if not doc:
        return {"found": False, "path": path}
    if _support_gate(authorization, x_active_org):
        return {"found": True, "path": path, "doc": doc, "full": True}
    # Trimmed user-facing view only.
    return {"found": True, "path": path, "full": False,
            "doc": {"page_key": doc.get("page_key"), "title": doc.get("title"),
                    "module": doc.get("module"), "user_md": doc.get("user_md")}}


@router.get("/support-docs")
def support_docs_list(authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                            org: str = ""):
    """List help docs for the coverage/editor view (support-gated). Defaults to HOUSE (global) docs; pass
    ?org=<tenant> to view a tenant's overrides."""
    if not _support_gate(authorization, x_active_org):
        raise HTTPException(403, "The support docs editor is restricted to house support staff.")
    target = (org or "").strip() or ORG_ID
    try:
        docs = (sb().schema("core").table("support_doc").select("*")
                .eq("org_id", target).order("page_key").execute().data) or []
    except Exception as e:
        raise HTTPException(500, f"support_doc unavailable (run migration 715?): {e}")
    return {"docs": docs, "org_id": target}


def _clean_doc(body: dict) -> dict:
    return {k: body[k] for k in _DOC_FIELDS if k in body}


@router.post("/support-docs")
def support_docs_upsert(body: dict, authorization: str = Header(default=""),
                              x_active_org: str = Header(default="")):
    """Create/update one help doc (support-gated). Keyed by (org_id, page_key); defaults to the HOUSE
    (global) org unless an explicit org_id is supplied (a per-tenant override)."""
    if not _support_gate(authorization, x_active_org):
        raise HTTPException(403, "Editing help docs is restricted to house support staff.")
    page_key = (body.get("page_key") or "").strip()
    if not page_key:
        raise HTTPException(422, "page_key is required")
    org_id = (body.get("org_id") or "").strip() or ORG_ID
    row = {**_clean_doc(body), "page_key": page_key, "org_id": org_id,
           "updated_by": (body.get("updated_by") or None),
           "updated_at": datetime.now(timezone.utc).isoformat()}
    try:
        sb().schema("core").table("support_doc").upsert(row, on_conflict="org_id,page_key").execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 715 first: {e}")
    return {"ok": True, "org_id": org_id, "page_key": page_key}


@router.delete("/support-docs/{did}")
def support_docs_delete(did: str, authorization: str = Header(default=""),
                              x_active_org: str = Header(default="")):
    if not _support_gate(authorization, x_active_org):
        raise HTTPException(403, "Editing help docs is restricted to house support staff.")
    sb().schema("core").table("support_doc").delete().eq("id", did).execute()
    return {"deleted": True}


@router.post("/support-docs/import")
def support_docs_import(body: dict, authorization: str = Header(default=""),
                              x_active_org: str = Header(default="")):
    """Bulk-import a domain content pack (support-gated). Contract:
        {"domain": str, "pages": [{"page_key","title","module","user_md","support_md",
          "common_issues":[{"symptom","diagnosis","fix","escalate_when"}],
          "permissions_needed","related_settings"}]}
    Upserts each page as a HOUSE (global) doc keyed by page_key. This is how the six domain packs land."""
    if not _support_gate(authorization, x_active_org):
        raise HTTPException(403, "Importing help docs is restricted to house support staff.")
    org_id = (body.get("org_id") or "").strip() or ORG_ID
    pages = body.get("pages") or []
    if not isinstance(pages, list) or not pages:
        raise HTTPException(422, "pages[] required")
    now = datetime.now(timezone.utc).isoformat()
    rows, skipped = [], 0
    for p in pages:
        if not isinstance(p, dict):
            skipped += 1
            continue
        pk = (p.get("page_key") or "").strip()
        if not pk:
            skipped += 1
            continue
        rows.append({**_clean_doc(p), "page_key": pk, "org_id": org_id,
                     "is_published": bool(p.get("is_published", True)),
                     "updated_by": f"import:{(body.get('domain') or 'pack')}"[:200], "updated_at": now})
    if not rows:
        raise HTTPException(422, "no valid pages (each needs a page_key)")
    try:
        sb().schema("core").table("support_doc").upsert(rows, on_conflict="org_id,page_key").execute()
    except Exception as e:
        raise HTTPException(500, f"import failed — run migration 715 first: {e}")
    return {"ok": True, "imported": len(rows), "skipped": skipped, "domain": body.get("domain")}


@router.post("/support-docs/seed-bundled")
def support_docs_seed_bundled(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Re-run the BUNDLED help-doc seed (app/data/support_docs_seed.json) into the HOUSE org on demand
    (support-gated). This is the same never-clobber load that runs automatically on the house org's
    sync_tenant pass — a human-edited row (updated_by not NULL/'seed') is never overwritten. Zero manual
    steps are needed after mig 715 + deploy; this endpoint just lets support re-seed after editing the
    bundle. Returns {inserted, updated, skipped, ok}."""
    if not _support_gate(authorization, x_active_org):
        raise HTTPException(403, "Seeding help docs is restricted to house support staff.")
    from app.modules.core.support_seed import seed_support_docs
    return seed_support_docs(sb(), ORG_ID)


# ── Import health + admin attention (mig 717, owner directive 2026-07-25) ────────────────────────
# Universal import-freshness registry + the consolidated admin-attention feed behind the login popup.
# Mounted ONTO this router rather than in main.py so the SHARED-file footprint of the feature is ZERO:
# main.py already does app.include_router(core_router, prefix="/api/v1"), and import_health.router
# carries no prefix of its own, so its paths resolve to /api/v1/core/import-feeds, /api/v1/core/attention.
# The sub-module imports core.router only LAZILY (inside functions), so there is no import cycle.
from app.modules.core import import_health as _import_health   # noqa: E402  (bottom-of-file mount)

router.include_router(_import_health.router)

# ── Auto-Fix Pipeline, Phase 1 (mig 718, owner-approved in chat 2026-07-30) ──────────────────────
# The fix-request registry + cost accounting behind /admin/fix-requests. Mounted ONTO this router (same
# rationale as import_health above): main.py needs no change, and the sub-router's own "/fix-pipeline"
# prefix resolves its paths to /api/v1/core/fix-pipeline/*. It imports core.router only LAZILY (inside
# functions), so there is no import cycle. NOTE: these paths are middleware-allowlisted (the agent door
# carries no JWT), so EVERY route in that module self-gates via its own _authorize() — see the module
# docstring and harness_fix_pipeline.py's route-coverage proof.
from app.modules.core import fix_pipeline as _fix_pipeline   # noqa: E402  (bottom-of-file mount)

router.include_router(_fix_pipeline.router)

# ── Training Center — guided walk-throughs (mig 720, owner directive 2026-08-04) ──────────────────
# Tours-as-data + the Phase-2 recording-script export behind /training and /admin/training. Mounted
# ONTO this router (same rationale as import_health / fix_pipeline above): main.py needs no change, and
# the sub-router's own "/training" prefix resolves its paths to /api/v1/core/training/*. It imports
# core.router only LAZILY (inside functions), so there is no import cycle. NOT middleware-allowlisted:
# every route carries full tenant-middleware protection like the rest of /core.
from app.modules.core import training as _training   # noqa: E402  (bottom-of-file mount)

router.include_router(_training.router)

# ── What's New — new features + improvements for ADMIN STAFF (mig 721, owner directive 2026-08-04) ──
# The other two panes beside the login WARNINGS. Mounted onto this router for the same reason as the
# three above: main.py (SHARED) needs no change, and the sub-router's "/whats-new" prefix resolves to
# /api/v1/core/whats-new*. Lazy imports only, so there is no import cycle. NOT allowlisted in the
# middleware: /whats-new/ingest self-gates (secret OR super-admin) and today is reachable by a
# super-admin JWT only — the tokenless secret path needs the one-line allowlist filed for the operator.
from app.modules.core import whats_new as _whats_new   # noqa: E402  (bottom-of-file mount)

router.include_router(_whats_new.router)

# Platform-core's OWN attention providers (tenant provisioning + system-error backlog). Imported purely
# for the @register_provider side effect — no routes, no gate, no aggregation change. Each of notify /
# helpdesk registers its own providers from its own module file the same way (see their routers' tails).
from app.modules.core import platform_attention as _platform_attention   # noqa: E402,F401
