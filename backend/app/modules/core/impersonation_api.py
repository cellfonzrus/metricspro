"""ADMIN "VIEW AS EMPLOYEE" — the console endpoints (start / stop / re-auth / audit / policy).

Owner directive 2026-08-06. The MECHANISM (grant minting, verification, the identity swap, the
clock-in/out carve-out) lives in `app/core/impersonation.py`; this module is only the door.

MOUNTING: this sub-router is mounted ONTO core/router.py's router (which already carries "/core"), so
main.py (SHARED) needs no change and the paths resolve to /api/v1/core/impersonation/*. It imports
core.router only LAZILY (inside functions) so there is no import cycle.

WHO MAY IMPERSONATE — the `impersonate` role permission (DEFAULT-DENY for everyone)
──────────────────────────────────────────────────────────────────────────────────
`storeops.roles.permissions.impersonate === true`. It is NOT implied by `scope: 'all'`, NOT implied by
the `admin` module, and NOT implied by super-admin — unlike every other gate in this codebase, there
is no bypass. Nothing seeds it (see mig 730's closing note), so on the day this ships NOBODY can
impersonate until an administrator consciously ticks "Sign in as an employee" on a role at
/admin/roles. That is deliberate: the owner asked for a debugging aid, and a debugging aid that is on
by default for every existing admin role is a standing cross-account login.

REACH — which tenants
─────────────────────
  • a normal permission-holder reaches ONLY the orgs where THAT membership's role holds the
    permission (a Luxelink manager can never view a Cellfonz employee);
  • a SUPER-ADMIN who ALSO holds the permission on one of their own memberships reaches every tenant,
    which matches how super-admin already works for /admin/tenants and the cross-tenant switcher.
The acting tenant is the `org_id` QUERY PARAM (contract §2) — rewritten from the JWT for a normal
user, honored from the switcher for a super-admin.

WHO MAY BE IMPERSONATED
───────────────────────
An ACTIVE `storeops.app_users` row WITH a login, in the acting org, that is neither the caller
themselves, nor a super-admin, nor a holder of the `impersonate` permission. The last two exclusions
close the escalation chain (borrow a face that is more powerful than your own).

THIS WHOLE PREFIX IS REFUSED INSIDE AN IMPERSONATED SESSION
──────────────────────────────────────────────────────────
`tenant_middleware` rejects EVERY method under /api/v1/core/impersonation when the request carries a
grant, so an impersonated session can neither nest another impersonation nor stop/inspect one. The
console is therefore always driven by the admin's OWN identity: /stop and /reauth are called WITHOUT
the x-impersonate header and identify the session by its id (the frontend's `api()` mirrors this by
never attaching the header to this prefix).
"""
import hashlib
import json
import time
import base64
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Header, Request

from app.core.database import get_supabase, get_supabase_admin
from app.core import impersonation as _imp

router = APIRouter(prefix="/impersonation", tags=["Core / Impersonation"])

ORG_ID = "00000000-0000-0000-0000-000000000001"
PERMISSION_KEY = "impersonate"      # storeops.roles.permissions.impersonate — DEFAULT-DENY, no bypass


def sb():
    return get_supabase()


# ── Authority resolution ────────────────────────────────────────────────────────────────────────
def can_impersonate(perms) -> bool:
    """PURE mirror of the frontend `canImpersonate` (rbac.ts) — KEEP IN SYNC. Explicit grant only:
    no super-admin bypass, no scope-'all' default, no `modules.admin` implication."""
    try:
        return (perms or {}).get(PERMISSION_KEY) is True
    except Exception:
        return False


def _role_perms(client, org_id, role):
    if not role:
        return {}
    try:
        rr = (client.schema("storeops").table("roles").select("permissions")
              .eq("org_id", org_id).eq("name", role).limit(1).execute().data) or []
        return (rr[0].get("permissions") or {}) if rr else {}
    except Exception:
        return {}


def _authority(client, uid):
    """(super_admin, {org_id: perms}) for every membership whose role HOLDS the permission.
    `super_admin` follows the platform rule used by `_require_super_admin` and the middleware: the
    flag on ANY membership row (it is a login-level bypass, not a per-tenant grant)."""
    from app.modules.core.router import _memberships
    rows = _memberships(client, uid) or []
    sa = any(r.get("super_admin") for r in rows)
    granted = {}
    for r in rows:
        org = str(r.get("org_id") or "")
        if not org:
            continue
        perms = _role_perms(client, org, r.get("role"))
        if can_impersonate(perms):
            granted[org] = perms
    return sa, granted


def _require_impersonator(authorization: str, org_id: str):
    """Resolve + authorize the REAL caller for acting on `org_id`. Returns (uid, membership_row).
    Raises 401/403. NEVER uses the impersonation-swapped identity (see `_real_uid`)."""
    uid = _real_uid(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    _imp.assert_not_impersonating("starting or managing an impersonation session")
    client = sb()
    sa, granted = _authority(client, uid)
    if not granted:
        raise HTTPException(403, "Your role does not have the 'Sign in as an employee' permission. "
                                 "An administrator can enable it per role on Roles & Access.")
    if org_id not in granted and not sa:
        raise HTTPException(403, "You can only sign in as an employee of a company you administer.")
    from app.modules.core.router import _memberships, _pick_membership
    rows = _memberships(client, uid) or []
    me = next((r for r in rows if str(r.get("org_id")) == str(org_id)), None) or _pick_membership(rows, None)
    return uid, (me or {})


def _real_uid(authorization: str):
    """The caller's OWN auth id, never the impersonation-swapped one. `_uid_from_token` applies the
    swap; `_real_uid_from_token` is the pre-swap resolver this module must use everywhere."""
    from app.modules.core.router import _real_uid_from_token
    return _real_uid_from_token(authorization)


def _client_ip(request):
    try:
        fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        return (fwd or (request.client.host if request.client else ""))[:64]
    except Exception:
        return ""


def _ua(request):
    try:
        return (request.headers.get("user-agent") or "")[:300]
    except Exception:
        return ""


# ── Roster (RULE THREE: the pick-don't-type option source) ──────────────────────────────────────
@router.get("/targets")
def list_targets(org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Employees of the ACTING tenant that may be viewed as, shaped for `EntityPicker`
    ([{id,label,sublabel}]). `id` is the auth id (the canonical key — emails repeat across tenants).
    "First Last", disambiguated by email exactly as RULE THREE requires (EntityPicker appends the
    sublabel automatically when two labels collide)."""
    _require_impersonator(authorization, org_id)
    client = sb()
    try:
        rows = (client.schema("storeops").table("app_users")
                .select("id,auth_id,email,full_name,role,is_active,super_admin")
                .eq("org_id", org_id).order("full_name").execute().data) or []
    except Exception:
        return {"targets": [], "ready": False}
    uid = _real_uid(authorization)
    # One roles read for the whole org rather than one per user.
    perms_by_role = {}
    try:
        for rr in ((client.schema("storeops").table("roles").select("name,permissions")
                    .eq("org_id", org_id).execute().data) or []):
            perms_by_role[rr.get("name")] = rr.get("permissions") or {}
    except Exception:
        perms_by_role = {}
    out = []
    for r in rows:
        aid = str(r.get("auth_id") or "")
        if not aid or aid == str(uid):
            continue                                    # no login / yourself
        if r.get("is_active") is False:
            continue
        if r.get("super_admin"):
            continue                                    # never borrow a platform-wide identity
        if can_impersonate(perms_by_role.get(r.get("role")) or {}):
            continue                                    # never borrow another impersonator's identity
        name = (r.get("full_name") or "").strip() or (r.get("email") or "").strip()
        out.append({"id": aid, "label": name, "sublabel": r.get("email") or "",
                    "role": r.get("role") or ""})
    return {"targets": out, "ready": True}


# ── Start / stop ────────────────────────────────────────────────────────────────────────────────
@router.post("/start")
def start(body: dict, request: Request, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Begin a "view as employee" session. Returns the signed grant the client presents as
    `x-impersonate` on every subsequent request.

    FAIL-CLOSED ORDER (this ordering is the audit guarantee): the immutable session row is written
    FIRST; only if that write succeeds is a grant minted. If mig 730 is un-run, or the audit store is
    unreachable, the write raises and NO grant exists — impersonation simply cannot happen."""
    uid, me = _require_impersonator(authorization, org_id)
    # The whole mechanism is enforced by TenantScopeMiddleware, which is inert when
    # MULTI_TENANT_ENFORCE is off. Minting a grant that nothing would honour would drop the admin
    # into a session that silently still acts as themselves — refuse instead of confusing them.
    from app.core.tenant_middleware import _enabled as _mw_enabled
    if not _mw_enabled():
        raise HTTPException(503, "Viewing the app as an employee needs tenant enforcement to be on "
                                 "(MULTI_TENANT_ENFORCE). Ask your operator to enable it.")
    target_id = str(body.get("target") or body.get("target_auth_id") or "").strip()
    if not target_id:
        raise HTTPException(400, "Choose the employee you want to view the app as.")
    client = sb()
    pol = _imp.load_policy(client, org_id)
    if not pol.get("enabled", True):
        raise HTTPException(403, "Impersonation is turned off for this company.")
    if target_id == str(uid):
        raise HTTPException(400, "That is your own login.")
    try:
        trows = (client.schema("storeops").table("app_users")
                 .select("id,auth_id,email,full_name,role,is_active,super_admin")
                 .eq("org_id", org_id).eq("auth_id", target_id).limit(1).execute().data) or []
    except Exception:
        raise HTTPException(503, "The employee directory is temporarily unavailable. Nothing was changed.")
    if not trows:
        raise HTTPException(404, "That employee is not part of this company.")
    t = trows[0]
    if t.get("is_active") is False:
        raise HTTPException(403, "That employee's access is disabled.")
    if t.get("super_admin"):
        raise HTTPException(403, "A platform super-admin cannot be viewed as.")
    if can_impersonate(_role_perms(client, org_id, t.get("role"))):
        raise HTTPException(403, "That employee's role can itself sign in as others, so it cannot be borrowed.")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=int(pol.get("max_minutes") or 45))
    sid = _imp.new_id()
    row = {
        "id": sid, "org_id": org_id,
        "actor_auth_id": str(uid), "actor_email": me.get("email"),
        "actor_name": me.get("full_name"), "actor_role": me.get("role"),
        "target_auth_id": target_id, "target_app_user": str(t.get("id") or ""),
        "target_email": t.get("email"), "target_name": t.get("full_name"), "target_role": t.get("role"),
        "reason": (str(body.get("reason") or "").strip() or None),
        "started_at": now.isoformat(), "expires_at": expires.isoformat(),
        "ip": _client_ip(request), "user_agent": _ua(request),
    }
    try:
        client.schema("core").table("impersonation_session").insert(row).execute()
    except Exception:
        # NO GRANT IS MINTED. An impersonation we cannot record must not happen (owner requirement).
        raise HTTPException(503, "Could not start: the impersonation audit record could not be written, "
                                 "so the session was refused. (If this is a new deployment, migration "
                                 "730 has not been run yet.)")
    _imp.log_event(kind="start", org_id=org_id, session_id=sid, actor_uid_=str(uid),
                   target_uid_=target_id, ip=row["ip"], user_agent=row["user_agent"],
                   detail={"target_email": t.get("email"), "reason": row["reason"],
                           "expires_at": row["expires_at"]}, client=client)
    grant = _imp.mint_grant(session_id=sid, actor_uid=str(uid), target_uid=target_id,
                            org_id=org_id, exp_ts=expires.timestamp())
    return {"ok": True, "grant": grant, "session": _public_session(row), "policy": pol}


@router.post("/stop")
def stop(body: dict, request: Request, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """End a session (the banner's "Exit / return to my account"). Called with the ADMIN's own token
    and NO x-impersonate header — the whole prefix is refused inside an impersonated session — so the
    stop is always recorded against the real human. Idempotent: stopping an already-ended session is
    a success, so a double click / a stale tab never shows an error."""
    uid = _real_uid(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    _imp.assert_not_impersonating("ending an impersonation session")
    sid = str(body.get("session_id") or "").strip()
    if not sid:
        raise HTTPException(400, "session_id is required")
    client = sb()
    try:
        rows = (client.schema("core").table("impersonation_session")
                .select("*").eq("id", sid).limit(1).execute().data) or []
    except Exception:
        raise HTTPException(503, "The impersonation audit store is temporarily unavailable.")
    if not rows:
        raise HTTPException(404, "No such session.")
    s = rows[0]
    sa, granted = _authority(client, str(uid))
    if str(s.get("actor_auth_id")) != str(uid) and not sa:
        raise HTTPException(403, "Only the administrator who started that session (or a super-admin) can end it.")
    if not s.get("ended_at"):
        try:
            (client.schema("core").table("impersonation_session")
             .update({"ended_at": datetime.now(timezone.utc).isoformat(),
                      "end_reason": str(body.get("reason") or "exit")[:32]})
             .eq("id", sid).is_("ended_at", "null").execute())
        except Exception:
            raise HTTPException(503, "Could not end the session cleanly — please retry.")
        _imp.log_event(kind="stop", org_id=s.get("org_id"), session_id=sid,
                       actor_uid_=str(s.get("actor_auth_id")), target_uid_=str(s.get("target_auth_id")),
                       ip=_client_ip(request), user_agent=_ua(request), client=client)
    _imp.invalidate_session(sid)
    return {"ok": True, "session_id": sid}


@router.get("/status")
def status(org_id: str = ORG_ID, authorization: str = Header(default="")):
    """The caller's own OPEN sessions (if any). Lets the banner rebuild itself after a browser
    refresh and lets the admin close a session they left running in another tab."""
    uid = _real_uid(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    now = datetime.now(timezone.utc)
    try:
        rows = (client.schema("core").table("impersonation_session").select("*")
                .eq("actor_auth_id", str(uid)).is_("ended_at", "null")
                .order("started_at", desc=True).limit(10).execute().data) or []
    except Exception:
        return {"open": [], "ready": False}
    live = [r for r in rows if _imp._iso_to_ts(r.get("expires_at")) > now.timestamp()]
    return {"open": [_public_session(r) for r in live], "ready": True}


def _public_session(r: dict) -> dict:
    """What the UI is allowed to see about a session. No tokens, ever."""
    return {"id": r.get("id"), "org_id": r.get("org_id"),
            "target_auth_id": r.get("target_auth_id"), "target_name": r.get("target_name"),
            "target_email": r.get("target_email"), "target_role": r.get("target_role"),
            "actor_email": r.get("actor_email"), "actor_name": r.get("actor_name"),
            "started_at": r.get("started_at"), "expires_at": r.get("expires_at"),
            "ended_at": r.get("ended_at"), "end_reason": r.get("end_reason"),
            "reason": r.get("reason"), "ip": r.get("ip")}


# ── The clock-in / clock-out carve-out: re-authenticate AS THE EMPLOYEE ─────────────────────────
def _jwt_claims(token: str) -> dict:
    """Read a JWT's payload WITHOUT verifying it. Safe here ONLY because the token has ALREADY been
    verified by Supabase (`auth.get_user`) on the line above every call site — this is used purely to
    read `iat` / `session_id`, never to make a trust decision on its own."""
    try:
        parts = str(token or "").split(".")
        if len(parts) < 2:
            return {}
        seg = parts[1]
        return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)).decode())
    except Exception:
        return {}


@router.post("/reauth")
def reauth(body: dict, request: Request, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Prove the EMPLOYEE just entered their own password, and mint ONE unlock for a clock-in or
    clock-out. This is the owner's carve-out, enforced server-side.

    HOW THE PROOF WORKS. The browser signs the employee in on a THROWAWAY anon Supabase client
    (`persistSession:false`, its own `storageKey`) so the admin's live session is untouched — the same
    pattern the kiosk manager-override already uses — and posts the resulting access token here as
    `{"session_id": ..., "token": ...}`. The password itself never reaches this API. The token is then
    verified SERVER-SIDE against Supabase and must:
      • resolve to the SESSION'S TARGET (not the admin, not anyone else);
      • be FRESH — issued within `reauth_token_max_age_s` (default 120 s), so a token captured from an
        earlier unlock is useless;
      • come from a Supabase sign-in session that has never minted an unlock for this impersonation
        session (UNIQUE(imp_session_id, auth_session_id) in mig 730), so holding on to a refresh token
        cannot manufacture a second punch.
    The returned marker is single-use and expires in `reauth_minutes` (default 5). It is consumed by
    `app.core.impersonation.require_target_reauth()` inside the clock-in / clock-out handler."""
    uid = _real_uid(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    _imp.assert_not_impersonating("unlocking clock in/out")
    sid = str(body.get("session_id") or "").strip()
    emp_token = str(body.get("token") or "").strip()
    if not sid or not emp_token:
        raise HTTPException(400, "session_id and token are required")
    client = sb()
    try:
        rows = (client.schema("core").table("impersonation_session").select("*")
                .eq("id", sid).limit(1).execute().data) or []
    except Exception:
        raise HTTPException(503, "The impersonation audit store is temporarily unavailable.")
    if not rows:
        raise HTTPException(404, "No such session.")
    s = rows[0]
    if str(s.get("actor_auth_id")) != str(uid):
        raise HTTPException(403, "That session belongs to a different administrator.")
    if s.get("ended_at") or _imp._iso_to_ts(s.get("expires_at")) <= time.time():
        raise HTTPException(403, "That impersonation session is no longer active.")
    pol = _imp.load_policy(client, str(s.get("org_id") or org_id))
    # 1) verify the EMPLOYEE's token server-side
    try:
        resp = get_supabase_admin().auth.get_user(emp_token)
        user = getattr(resp, "user", None) or resp
        tuid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    except Exception:
        tuid = None
    if not tuid or str(tuid) != str(s.get("target_auth_id")):
        raise HTTPException(403, "That password did not match the employee you are viewing the app as.")
    # 2) freshness — the sign-in must have JUST happened
    claims = _jwt_claims(emp_token)
    iat = float(claims.get("iat") or 0)
    max_age = int(pol.get("reauth_token_max_age_s") or 120)
    if iat and (time.time() - iat) > max_age:
        raise HTTPException(403, "That sign-in is too old. Ask the employee to enter their password again.")
    # 3) one unlock per Supabase sign-in session (replay control)
    auth_sid = str(claims.get("session_id") or claims.get("jti") or "").strip() \
        or hashlib.sha256(emp_token.encode()).hexdigest()[:48]
    nonce = _imp.new_id()
    exp = datetime.now(timezone.utc) + timedelta(minutes=int(pol.get("reauth_minutes") or 5))
    try:
        (client.schema("core").table("impersonation_reauth").insert({
            "id": _imp.new_id(), "imp_session_id": sid, "org_id": s.get("org_id"),
            "target_auth_id": str(tuid), "nonce": nonce, "auth_session_id": auth_sid,
            "expires_at": exp.isoformat(), "ip": _client_ip(request)}).execute())
    except Exception:
        # Either mig 730 is un-run (⇒ no session could exist, so unreachable) or the UNIQUE
        # (imp_session_id, auth_session_id) fired: this sign-in already bought an unlock.
        raise HTTPException(409, "That sign-in has already been used once. Ask the employee to enter "
                                 "their password again for another clock in/out.")
    _imp.log_event(kind="reauth", org_id=s.get("org_id"), session_id=sid, actor_uid_=str(uid),
                   target_uid_=str(tuid), ip=_client_ip(request), user_agent=_ua(request),
                   detail={"expires_at": exp.isoformat()}, client=client)
    marker = _imp.mint_reauth(session_id=sid, target_uid=str(tuid), nonce=nonce,
                              exp_ts=exp.timestamp())
    return {"ok": True, "reauth": marker, "expires_at": exp.isoformat(),
            "valid_minutes": int(pol.get("reauth_minutes") or 5), "single_use": True}


# ── Audit log (viewable in the admin UI — requirement 4) ────────────────────────────────────────
def _can_view_log(client, uid, org_id) -> bool:
    from app.modules.core.router import _resolve_caller, _can_edit_setting
    caller = _resolve_caller(client, uid, org_id)
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    if _can_edit_setting(caller, "impersonation"):
        return True
    return can_impersonate(caller.get("perms") or {})


@router.get("/log")
def log(org_id: str = ORG_ID, limit: int = 100, session_id: str = "",
        authorization: str = Header(default="")):
    """The impersonation audit trail for a tenant: every session and, for one session, every write
    made inside it. Org-scoped (RULE ONE) — the org_id query param is the acting tenant."""
    uid = _real_uid(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    if not _can_view_log(client, uid, org_id):
        raise HTTPException(403, "not authorized to view the impersonation audit log")
    lim = max(1, min(int(limit or 100), 500))
    sessions, actions, ready = [], [], True
    try:
        q = (client.schema("core").table("impersonation_session").select("*").eq("org_id", org_id)
             .order("started_at", desc=True).limit(lim))
        sessions = [_public_session(r) for r in (q.execute().data or [])]
    except Exception:
        ready = False
    if session_id:
        try:
            actions = (client.schema("core").table("impersonation_action").select("*")
                       .eq("org_id", org_id).eq("session_id", session_id)
                       .order("at", desc=True).limit(500).execute().data) or []
        except Exception:
            actions = []
    return {"sessions": sessions, "actions": actions, "ready": ready,
            "policy": _imp.load_policy(client, org_id)}


# ── Tenant policy (RULE TWO — configurable, gated by the `impersonation` settings area) ────────
@router.get("/policy")
def get_policy(org_id: str = ORG_ID, authorization: str = Header(default="")):
    uid = _real_uid(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    from app.modules.core.router import _resolve_caller, _can_edit_setting
    client = sb()
    caller = _resolve_caller(client, uid, org_id)
    return {"policy": _imp.load_policy(client, org_id),
            "defaults": dict(_imp.DEFAULT_POLICY),
            "can_edit": bool(caller and _can_edit_setting(caller, "impersonation")),
            "can_impersonate": bool(caller and can_impersonate(caller.get("perms") or {}))}


@router.put("/policy")
def put_policy(body: dict, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """Edit the tenant's impersonation policy. Gated by the `impersonation` SETTING_AREA (so it can be
    granted per role at /admin/roles like every other settings area). Degrades honestly when mig 730
    has not added the column."""
    # The impersonation assert comes FIRST: `_require_setting` resolves the caller through the
    # EFFECTIVE (possibly swapped) identity, so checking "am I borrowing a face" before "may this
    # face edit settings" keeps the refusal reason honest.
    _imp.assert_not_impersonating("changing the impersonation policy")
    from app.modules.core.router import _require_setting
    _require_setting(authorization, org_id, "impersonation")
    pol = _imp.normalize_policy(body.get("policy") if isinstance(body.get("policy"), dict) else body)
    try:
        sb().schema("storeops").table("tenants").update({"impersonation_policy": pol}) \
            .eq("org_id", org_id).execute()
    except Exception:
        raise HTTPException(503, "Could not save — the impersonation policy column is not available yet "
                                 "(migration 730).")
    _imp.invalidate_policy(org_id)
    return {"ok": True, "policy": pol}
