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
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Header, Request
from app.core.database import get_supabase, get_supabase_admin
from app.core.config import settings
from app.modules.core.entitlements import (
    MODULE_CATALOG, ROLE_GATE_KEYS, load_module_catalog,
    sync_tenant, sync_all_tenants, needs_sync,
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

router = APIRouter(prefix="/core", tags=["Core / RBAC"])
ORG_ID = "00000000-0000-0000-0000-000000000001"


def sb():
    return get_supabase()


# ── App config: the master "enforce login" switch ─────────────────────────────────────
@router.get("/auth-config")
async def get_auth_config():
    """PUBLIC: tells the frontend whether to enforce login. Default false (app open) so the
    deploy never locks anyone out; the admin flips it on once everyone is provisioned.
    Returns false if migration 015 hasn't run yet (table missing)."""
    try:
        rows = sb().schema("storeops").table("app_config").select("rbac_enabled") \
            .eq("id", 1).limit(1).execute().data or []
        return {"rbac_enabled": bool(rows[0]["rbac_enabled"]) if rows else False}
    except Exception:
        return {"rbac_enabled": False}


@router.put("/auth-config")
async def set_auth_config(body: dict, org_id: str = ORG_ID):
    """Flip login enforcement on/off (from the Roles admin). Once ON, every user must sign in."""
    enabled = bool(body.get("rbac_enabled"))
    sb().schema("storeops").table("app_config").upsert(
        {"id": 1, "org_id": org_id, "rbac_enabled": enabled,
         "updated_at": datetime.now(timezone.utc).isoformat()}, on_conflict="id").execute()
    return {"rbac_enabled": enabled}


# ── Portal reports: which reports are surfaced in the employee portal + to which roles (mig 052) ──
@router.get("/portal-reports")
async def get_portal_reports(org_id: str = ORG_ID):
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
async def set_portal_report(body: dict, org_id: str = ORG_ID):
    """Upsert one report's portal config. Body: {href, enabled, roles[], label?, category?}."""
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
def _uid_from_token(authorization: str):
    """Validate the Supabase Auth JWT (server-side) and return its auth user id, or None."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        resp = get_supabase_admin().auth.get_user(token)
        user = getattr(resp, "user", None) or resp
        return getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    except Exception:
        return None


# ── Multi-tenant membership (platform-core-9) ──────────────────────────────────────────
# One Supabase login (auth_id) may hold an app_users row PER tenant it belongs to (mig 706). These
# `_memberships` (= list_memberships) and `_pick_membership` (= pick_membership) are imported from
# app.modules.core.membership at the top of this file — the single source of truth for the membership
# selection rule. They pick the membership row for the tenant the request is ACTING AS: the active
# tenant is declared by the client via the `x-active-org` header — UNTRUSTED, so it is honored ONLY
# when it names one of the login's memberships; otherwise the login's default membership is used. This
# mirrors the tenant-middleware rule exactly (single source of truth for "which tenant am I").


@router.get("/my-tenants")
async def my_tenants(authorization: str = Header(default="")):
    """The tenants this ONE login belongs to (drives the post-login tenant picker + the top-bar
    switcher). Resolves purely from the token's auth_id, so it works BEFORE an active tenant is
    chosen. A single-membership login returns exactly one row ⇒ the frontend shows no picker."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    rows = _memberships(client, uid)
    if not rows:
        return {"tenants": [], "count": 0}
    # tenant display names + per-membership role display (roles are per-org)
    orgs = [r.get("org_id") for r in rows if r.get("org_id")]
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
async def whoami(authorization: str = Header(default=""), x_active_org: str = Header(default=""),
                 x_2fa_token: str = Header(default="")):
    """The logged-in user's profile + resolved role permissions FOR THE ACTIVE TENANT. Token-verified
    — the frontend sends the Supabase session access token as `Authorization: Bearer <token>` and, for
    a login that belongs to >1 tenant, the chosen tenant as `x-active-org`. The active tenant is
    membership-checked (an x-active-org the login doesn't belong to falls back to the default
    membership) so a single-tenant login is entirely unaffected."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    rows = _memberships(client, uid)
    u = _pick_membership(rows, (x_active_org or "").strip() or None)
    if not u:
        return {"provisioned": False, "user": None, "permissions": {}}
    perms = {}
    if u.get("role"):
        rr = client.schema("storeops").table("roles").select("display_name,permissions") \
            .eq("org_id", u.get("org_id") or ORG_ID).eq("name", u["role"]).limit(1).execute().data or []
        if rr:
            perms = rr[0].get("permissions") or {}
            u["role_display"] = rr[0].get("display_name")
    # best-effort last_login stamp
    try:
        client.schema("storeops").table("app_users").update(
            {"last_login": datetime.now(timezone.utc).isoformat()}).eq("id", u["id"]).execute()
    except Exception:
        pass
    # Self-provision on login: if this tenant is behind the current SEED_VERSION, reconcile its
    # module entitlement + seed any newly-shipped default content. This is how a NEW feature
    # auto-propagates to every existing tenant (no per-feature migration needed). Cheap no-op once
    # the tenant is up to date (a single indexed lookup).
    try:
        org = u.get("org_id") or ORG_ID
        if needs_sync(client, org):
            sync_tenant(client, org)
    except Exception:
        pass
    # Tenant pay-period + onboarding-setup status (mig 085) — powers the "finish setup" banner
    # (banner only, nothing blocked) and lets the schedule/payroll derive the tenant's work-week.
    tenant = None
    try:
        t = _tenant_row(client, u.get("org_id") or ORG_ID)
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
              .eq("org_id", u.get("org_id") or ORG_ID).execute().data) or []
        carriers = [{"name": c.get("name"), "code": c.get("code"), "is_default": c.get("is_default")}
                    for c in cr if c.get("name")]
    except Exception:
        pass
    # Auth-hardening: the tenant password policy (client-side strength hints) + the 2FA gate for the
    # active tenant/user. Best-effort — un-run migs degrade to code defaults / 2FA off (no lockout).
    org_id = u.get("org_id") or ORG_ID
    pw_policy = _load_password_policy(client, org_id)
    tw_policy = _load_twofa_policy(client, org_id)
    twofa = {"required": _twofa_required_for(tw_policy, u.get("role"), u.get("twofa_enabled")),
             "verified": bool(_sec.twofa_token_valid_for(x_2fa_token, uid, org_id, _sec.now_ts())),
             "mode": tw_policy["mode"], "user_channels": u.get("twofa_channels") or ["email"]}
    return {"provisioned": True, "user": u, "permissions": perms,
            "active": bool(u.get("is_active", True)), "tenant": tenant, "carriers": carriers,
            "password_policy": pw_policy, "twofa": twofa}


@router.post("/me/password-changed")
async def password_changed(authorization: str = Header(default="")):
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
async def list_tenants(authorization: str = Header(default="")):
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
async def create_tenant(body: dict, authorization: str = Header(default="")):
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
async def list_super_admins(authorization: str = Header(default="")):
    """Super-admin: every login holding platform-wide (cross-tenant) access. These bypass tenant
    scoping (tenant_middleware honours super_admin), so this is the audit surface for who holds the keys."""
    _require_super_admin(authorization)
    rows = (sb().schema("storeops").table("app_users")
            .select("id,email,full_name,role,org_id,is_active,last_login")
            .eq("super_admin", True).order("email").execute().data) or []
    return {"super_admins": rows}


@router.post("/super-admins")
async def create_super_admin(body: dict, authorization: str = Header(default="")):
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
async def revoke_super_admin(email: str = "", authorization: str = Header(default="")):
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
async def list_modules():
    """PUBLIC: the canonical module registry (module_key → label). Single source of truth is
    core.module_catalog (mig 700), with an in-code fallback so an unrun migration is a no-op.
    Drives the billing plan editor's per-module picker and the tenant entitlement view."""
    cat = load_module_catalog(sb())
    return {"modules": [{"key": k, "label": v} for k, v in cat.items()]}


@router.post("/tenants/sync")
async def sync_tenants_endpoint(authorization: str = Header(default=""), x_notify_secret: str = Header(default="")):
    """Reconcile EVERY tenant — module entitlement (all-access default) + tenant-safe default
    content — bringing tenants created before a feature shipped up to date. Auth: super-admin,
    OR the NOTIFY_RUN_SECRET header (so a post-deploy / cron backfill can run without a UI token)."""
    if not (settings.NOTIFY_RUN_SECRET and x_notify_secret == settings.NOTIFY_RUN_SECRET):
        _require_super_admin(authorization)
    return sync_all_tenants(sb())


@router.post("/tenants/{org_id}/sync")
async def sync_one_tenant_endpoint(org_id: str, authorization: str = Header(default="")):
    """Super-admin: reconcile a SINGLE tenant's entitlement + default content."""
    _require_super_admin(authorization)
    return sync_tenant(sb(), org_id)


def _signups_open() -> bool:
    return os.environ.get("SIGNUPS_OPEN", "").lower() in ("1", "true", "yes")


@router.get("/signup-status")
async def signup_status():
    """PUBLIC: whether self-serve signup is open (env SIGNUPS_OPEN). The /signup page reads this."""
    return {"open": _signups_open()}


@router.post("/signup")
async def signup(body: dict):
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
async def update_tenant(org_id: str, body: dict, authorization: str = Header(default="")):
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

def _load_password_policy(client, org_id):
    """The tenant's effective password policy (override merged over owner defaults, all bounds clamped).
    Best-effort: any read error / un-run mig 709 → the code defaults (so enforcement is never lost)."""
    raw = None
    try:
        t = _tenant_row(client, org_id) or {}
        raw = t.get("password_policy")
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
    {"key": "targets",           "label": "Target Settings"},
    {"key": "closing",           "label": "Daily Closing / Tender Fields"},
    {"key": "kpi",               "label": "KPI Metrics"},
    {"key": "expenses",          "label": "Store Expenses"},
    {"key": "labels",            "label": "Display Labels"},
    {"key": "menu",              "label": "Menu Layout"},
    {"key": "failures",          "label": "Failure Logs (clock-in sensitivity, logged categories)"},
    {"key": "security",          "label": "Security (password policy · 2FA · admin-set passwords)"},
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
async def list_setting_areas(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
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
async def failure_types(authorization: str = Header(default="")):
    """The registry of known failure categories + default remediation (drives filters / UI)."""
    return {"types": [{"key": k, "label": v["label"], "severity": v["severity"],
                       "remediation": v["remediation"]} for k, v in FAILURE_TYPES.items()]}


@router.post("/failures")
async def record_failure(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
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
async def list_failures(authorization: str = Header(default=""), x_active_org: str = Header(default=""), status: str = "", category: str = "", limit: int = 300):
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    caller = _resolve_caller(client, uid, x_active_org)
    if not _can_view_failures(caller):
        raise HTTPException(403, "Failure Logs are admin-only. Grant the /failures page to a role to share it.")
    q = client.schema("core").table("failure_log").select("*").eq("org_id", caller["org_id"])
    if status:
        q = q.eq("status", status)
    if category:
        q = q.eq("category", category)
    try:
        rows = q.order("created_at", desc=True).limit(min(max(limit, 1), 1000)).execute().data or []
    except Exception as e:
        raise HTTPException(500, f"failure_log unavailable (run migration 112?): {e}")
    open_n = sum(1 for r in rows if r.get("status") == "open")
    return {"failures": rows, "open_count": open_n, "can_configure": _can_edit_setting(caller, "failures")}


@router.patch("/failures/{fid}")
async def update_failure(fid: str, body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
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
async def get_failures_config(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
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
async def put_failures_config(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
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


@router.get("/tenant-settings")
async def get_tenant_settings(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
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
async def put_tenant_settings(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
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
async def list_roles(org_id: str = ORG_ID):
    client = sb()
    try:
        _ensure_roles_for_levels(client, org_id)   # org-chart levels become assignable roles
    except Exception:
        pass  # never block the roles list if the level→role sync fails
    rows = client.schema("storeops").table("roles").select("*").eq("org_id", org_id) \
        .order("id").execute().data or []
    return {"roles": rows}


@router.post("/roles")
async def create_role(body: dict, org_id: str = ORG_ID):
    name = (body.get("name") or "").strip().lower().replace(" ", "_")
    if not name:
        raise HTTPException(400, "name required")
    row = {"org_id": org_id, "name": name,
           "display_name": body.get("display_name") or name.replace("_", " ").title(),
           "permissions": body.get("permissions") or {}}
    res = sb().schema("storeops").table("roles").upsert(row, on_conflict="org_id,name").execute()
    return (res.data or [{}])[0]


@router.put("/roles/{role_id}")
async def update_role(role_id: int, body: dict):
    upd = {}
    if "display_name" in body:
        upd["display_name"] = body["display_name"]
    if "permissions" in body:
        upd["permissions"] = body["permissions"]
    if not upd:
        raise HTTPException(400, "nothing to update")
    res = sb().schema("storeops").table("roles").update(upd).eq("id", role_id).execute()
    if not res.data:
        raise HTTPException(404, "role not found")
    return res.data[0]


@router.delete("/roles/{role_id}")
async def delete_role(role_id: int, org_id: str = ORG_ID):
    """Delete a custom role. Refuses to delete 'admin' (lock-out guard) and blocks deletion while any
    user is still assigned it (reassign them first) so nobody is silently orphaned."""
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
async def list_users(org_id: str = ORG_ID):
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


@router.post("/users/assign")
async def assign_role(body: dict, org_id: str = ORG_ID):
    """Upsert a core.users row (assign role + scope). Keyed on (org_id, email). Does NOT create
    the auth login — call /users/create-login (or bulk-provision) for that."""
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
async def bulk_assign(body: dict, org_id: str = ORG_ID):
    """Bulk upsert app_users (assign roles) from a list — powers the employee-sheet upload and
    the multi-add form. Body: {users:[{email, full_name, role, market, store_code}]}. Does NOT
    create logins (call /users/bulk-provision or per-row create-login after). Role names are
    validated against storeops.roles; bad rows are reported, the rest still apply."""
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
async def create_login(body: dict, org_id: str = ORG_ID):
    """Create (or relink) the Supabase Auth account for ONE assigned user and store auth_id. Returns
    an ACCESS CODE to hand out (user resets on first login). Consent-based (platform-core-11): if the
    email ALREADY has a MetricsPro login in another tenant, NO second login / alias / shared bind is
    minted — a pending account-link invite is recorded and the response is BYTE-IDENTICAL to a
    brand-new email (the admin can't learn the email exists elsewhere). The user then CONNECTs or
    DISABLEs on their own next sign-in. Pass {separate_login:true} to force a distinct tenant-aliased
    login (the isolated-per-tenant escape hatch, e.g. kiosk clock-punching reps)."""
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
async def pending_connections(authorization: str = Header(default="")):
    """Pending account-link invites addressed to the AUTHENTICATED caller's OWN email. Returns ONLY
    the inviting tenant's name per invite (zero cross-tenant disclosure — never the caller's other
    tenants, never who else an email belongs to). Empty for everyone without an invite, so the vast
    majority of logins never see this. Drives the post-login connect/disable prompt."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    email = _email_for_uid(client, uid)
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
async def password_policy_public():
    """PUBLIC: the owner DEFAULT password policy — drives client-side strength hints on UNAUTHENTICATED
    screens (signup / self-serve reset). The tenant's REAL policy (server-enforced) rides on /core/me;
    exposing only the default here avoids any per-email enumeration."""
    return {"policy": dict(_sec.DEFAULT_PASSWORD_POLICY), "hard_max": _sec.HARD_MAX_PASSWORD,
            "special_chars": _sec.SPECIAL_CHARS}


def _normalize_twofa_policy(raw):
    p = {"mode": "off", "channels": ["email"], "required_roles": []}
    if isinstance(raw, dict):
        m = str(raw.get("mode") or "off").lower()
        p["mode"] = m if m in ("off", "optional", "required") else "off"
        ch = raw.get("channels")
        if isinstance(ch, list):
            p["channels"] = [c for c in ch if c in ("email", "whatsapp", "sms")] or ["email"]
        rr = raw.get("required_roles")
        if isinstance(rr, list):
            p["required_roles"] = [str(r) for r in rr if str(r).strip()]
    return p


def _load_twofa_policy(client, org_id):
    try:
        t = _tenant_row(client, org_id) or {}
        return _normalize_twofa_policy(t.get("twofa_policy"))
    except Exception:
        return _normalize_twofa_policy(None)


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


@router.get("/security-settings")
async def get_security_settings(authorization: str = Header(default=""), x_active_org: str = Header(default="")):
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
async def put_security_settings(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
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
async def set_own_password(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
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
async def forgot_password(body: dict, request: Request):
    """PUBLIC: request a reset code. ANTI-ENUMERATION — the response is ALWAYS the same generic message
    (and same code path/timing) whether or not the account exists; existence, tenant membership and
    disabled status are NEVER revealed. Sends over email (+ verified WhatsApp phone if on file)."""
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
    await _anotify.send_reset_otp(email, code, channels=channels, phone=phone or "")
    _audit_auth_event(client, "reset_requested", email=email, org_id=org_id, actor="self")
    return _RESET_GENERIC


@router.post("/auth/reset-password")
async def reset_password_otp(body: dict):
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
async def reveal_code(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
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
async def get_2fa_status(authorization: str = Header(default=""), x_active_org: str = Header(default=""),
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
async def verify_2fa(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
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
async def set_2fa_settings(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """The user turns 2FA on/off for themselves (matters under the 'optional' tenant mode) and picks
    channels. Cannot turn OFF when the tenant policy is 'required'."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    u = _pick_membership(_memberships(client, uid), (x_active_org or "").strip() or None)
    if not u:
        raise HTTPException(403, "no tenant for this login")
    org_id = u.get("org_id") or ORG_ID
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
                    x_active_org: str = Header(default="")):
    """Set/replace the user's phone (unverified) and send a WhatsApp verification code. The phone becomes
    a usable 2FA channel only AFTER it's verified."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    u = _pick_membership(_memberships(client, uid), (x_active_org or "").strip() or None)
    if not u:
        raise HTTPException(403, "no tenant for this login")
    org_id = u.get("org_id") or ORG_ID
    email = (u.get("email") or _email_for_uid(client, uid) or "").strip().lower()
    phone = "".join(c for c in (body.get("phone") or "") if c.isdigit() or c == "+")
    if len(phone) < 8:
        raise HTTPException(400, "Enter a valid phone number (with country code).")
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
async def verify_phone(body: dict, authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Verify the phone with the WhatsApp code → mark it verified + enable WhatsApp as a 2FA channel."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    u = _pick_membership(_memberships(client, uid), (x_active_org or "").strip() or None)
    if not u:
        raise HTTPException(403, "no tenant for this login")
    org_id = u.get("org_id") or ORG_ID
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
async def reset_password(body: dict, authorization: str = Header(default="")):
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
async def reset_tenant_admin_password(org_id: str, body: dict = None, authorization: str = Header(default="")):
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
async def bulk_provision(body: dict, org_id: str = ORG_ID):
    """Create logins for every assigned core.users row that has an email and no auth_id yet
    (optionally limited to body['emails']). Returns the per-user temp passwords to distribute."""
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
async def delete_user(body: dict, org_id: str = ORG_ID):
    """Hard-delete an app user: remove the storeops.app_users row AND its Supabase Auth account."""
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
async def deactivate_user(body: dict, org_id: str = ORG_ID):
    """Soft-disable an app user (keeps the auth account; flip is_active)."""
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
async def purge_employee(body: dict, org_id: str = ORG_ID):
    """Delete or deactivate a person from BOTH the StoreOps roster and the Roles module in one
    call (the Roles & Access remove action). Identify by employee_pk (storeops.employees.id) for
    a real employee, or by email/employee_id for a manually-added Roles user (which has no
    employees row). mode='delete' hard-removes the employees row + login + Supabase Auth account;
    mode='deactivate' flips both is_active=False and revokes access (keeps the auth account)."""
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
    widgets = {k: True for k in EMP_WIDGETS}
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
async def set_employee_widget_overrides(body: dict, org_id: str = ORG_ID):
    """Per-employee Employee-Dashboard widget overrides (#1b). Body:
    {employee_id?, email?, widget_overrides: {widget_key: bool} | null}. Writes onto the
    person's storeops.app_users row (so they must be assigned a role first). null/{} = clear
    (inherit the role default). Unknown widget keys are dropped."""
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

