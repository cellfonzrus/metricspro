"""Tenant-scope middleware (SaaS P3 hardening) — GATED OFF by default.

When env MULTI_TENANT_ENFORCE=1, every request's org_id is DERIVED FROM THE VERIFIED TOKEN, not
trusted from the query string: the middleware verifies the Supabase bearer token, resolves the
caller's storeops.app_users.org_id, and REWRITES the org_id query param to that value — so a user
can't reach another tenant by passing a different org_id (the residual risk left by P2's client-side
scoping). Public routes are allowlisted; token→org_id is cached briefly to avoid a Supabase auth
call per request. Default OFF ⇒ pure pass-through, zero behavior change.

2026-07-14 hardening (owner-approved) — CLOSE THE UNAUTHENTICATED CROSS-TENANT HOLE:
Previously, a request with NO bearer token (or a bogus one) fell straight through with the caller's
own `?org_id=` honored — so an unauthenticated caller could read/write ANY tenant by guessing an
org_id. Now, when enforcement is on, a request to a NON-public route that does not carry a VALID
Supabase token is REJECTED (401 `{"detail":"authentication required"}`) instead of falling through.

  • The authenticated path is UNCHANGED: valid normal-user token → org_id rewrite; super-admin
    (or a verified user with no app_users row) → NO rewrite (client org_id honored, cross-tenant
    admin intact).
  • Only genuinely-anonymous / self-authenticating routes are allowlisted (see `_is_public`):
    health + docs; the pre-login core auth endpoints; token-verified /core/me; the self-serve
    signup pair; the dual-auth /core/tenants/sync; the Meta WhatsApp webhook; the HR onboarding
    public token endpoints; the notify no-login report download (/api/v1/notify/dl/{token} — the HMAC
    token IS the capability, scoped to one file); and every `*/run-due` sweep (each verifies its own
    x-notify-secret and is called by pg_cron with no JWT). Each of these authenticates by another
    mechanism (its own token/secret/handshake), so bypassing the JWT check for them is safe.
  • KILL SWITCH: env REQUIRE_AUTH (default ON when unset). Set REQUIRE_AUTH=0 to revert to the old
    pass-through (client org_id honored on tokenless requests) via a single Railway env change — no
    code rollback. The authenticated rewrite still runs regardless.

2026-07-14 multi-tenant login switcher (platform-core-9) — ONE login may belong to MANY tenants:
A login's org is no longer a single app_users.org_id; it is the SET of tenants the auth_id is a
member of (one storeops.app_users row per tenant, mig 706). The caller declares which tenant it is
acting as via the `x-active-org` request header. The middleware VERIFIES that choice against the
membership set (never trusts a bare client value): if the header names a tenant the login belongs to,
org_id is rewritten to it; otherwise it falls back to the login's DEFAULT membership (is_default_org,
else earliest-created). A single-membership login (which includes every mig-088 aliased login) always
resolves to its one org regardless of the header — so nothing changes for them. Super-admins still
bypass (no rewrite, client org_id honored). The token→identity cache holds the whole membership set,
so switching tenants (a new header value on the same token) needs no re-auth and no cache bust.
"""
import os
import time
import asyncio
from urllib.parse import parse_qs, urlencode

# Exact full paths that are public (matched literally, no prefix semantics).
_PUBLIC_EXACT = frozenset({
    "/health",                            # Railway / uptime health probe (no auth by design)
    "/openapi.json",                      # OpenAPI schema backing the docs UIs (currently open)
    "/api/v1/core/auth-config",           # login-enforcement flag read by the login/layout BEFORE sign-in
    "/api/v1/core/signup",                # self-serve tenant signup (env-gated SIGNUPS_OPEN; anonymous)
    "/api/v1/core/signup-status",         # /signup page checks whether signups are open, pre-login
    "/api/v1/core/tenants/sync",          # dual-auth: NOTIFY_RUN_SECRET header OR super-admin; cron has no JWT
    "/api/v1/core/password-policy/public",  # PUBLIC: owner DEFAULT policy for pre-login strength hints
    "/api/v1/core/auth/forgot-password",  # PUBLIC self-serve reset request (anti-enumeration; anonymous)
    "/api/v1/core/auth/reset-password",   # PUBLIC self-serve reset completion (code-gated; anonymous)
    "/api/v1/core/bootstrap",             # ONE-call login bootstrap (auth-config + my-tenants +
                                          # pending-connections + me). Same rationale as the
                                          # /api/v1/core/me prefix below: it SELF-GATES on the bearer
                                          # token inside the handler (401 without a valid one), and it
                                          # must be reachable BEFORE the 2FA marker exists so a
                                          # 2FA-required login can learn it needs the OTP step.
})

# Public path PREFIXES, matched at a SEGMENT BOUNDARY only (path == p or path.startswith(p + "/")),
# so "/api/v1/core/me" allows "/api/v1/core/me" and "/api/v1/core/me/password-changed" but NOT a
# hypothetical "/api/v1/core/members" — no sloppy startswith over-matching.
_PUBLIC_PREFIXES = (
    "/docs",                              # Swagger UI + /docs/oauth2-redirect (currently open)
    "/redoc",                             # ReDoc UI (currently open)
    "/api/v1/core/me",                    # token-verified whoami + /me/password-changed (self-gate on token)
    "/api/v1/remediation/whatsapp-webhook",  # Meta webhook: GET verify handshake + POST receive (self-gates)
    "/api/v1/hr/public/onboarding",       # HR onboarding public token endpoints (the link token IS the auth)
    "/api/v1/notify/dl",                   # no-login report download: the HMAC token IS the auth; reaches
                                           # ONLY the one artifact it signs (uniform 404 on any bad token)
)

# Self-authenticating background sweeps: EVERY route ending in "/run-due" is invoked by pg_cron with
# no JWT and verifies its own `x-notify-secret` header inside the handler. Allowlisting the suffix
# lets the cron reach the handler (which still 403s on a wrong/absent secret) and is robust to new
# sweeps added by other modules. The full current set is enumerated in the platform-core handoff.
_RUN_DUE_SUFFIX = "/run-due"

# token -> (identity, expiry_epoch), positive results only. identity is the 4-tuple returned by
# _resolve_identity: (authenticated, super_admin, member_orgs, default_org). Caching the whole
# membership SET (not a single org) lets a login switch active tenants — a new x-active-org header on
# the SAME token — with no re-auth and no cache bust; the per-request header pick is pure/cheap.
_cache: dict = {}
_TTL = 60.0

# Header by which the client declares which of its tenants it is acting as. Client-supplied and
# therefore UNTRUSTED — always verified against the login's membership set before it is honored.
_ACTIVE_ORG_HEADER = "x-active-org"


def _enabled() -> bool:
    return os.environ.get("MULTI_TENANT_ENFORCE", "").lower() in ("1", "true", "yes")


def _require_auth() -> bool:
    """Kill switch. Default ON when unset; REQUIRE_AUTH=0/false/no/off reverts to old pass-through."""
    return os.environ.get("REQUIRE_AUTH", "1").lower() not in ("0", "false", "no", "off")


# ── 2FA enforcement (auth-hardening 2026-07-17) — ADDITIVE, super-admin always bypassed ─────────────
# Break-glass: TWOFA_ENFORCE default ON, but per-tenant policy defaults OFF (NULL twofa_policy → mode
# 'off'), so a deploy locks NObody out. Set TWOFA_ENFORCE=0 to kill enforcement globally (a bad deploy
# can't strand the owner). A missing mig 711 column → the policy read errors → treated 'off' → no-op.
_twofa_cache: dict = {}          # org_id -> (policy_dict, expiry_epoch)
_TWOFA_TTL = 60.0


def _twofa_enforce() -> bool:
    return os.environ.get("TWOFA_ENFORCE", "1").lower() not in ("0", "false", "no", "off")


def _tenant_2fa_policy(org: str) -> dict:
    """Best-effort cached read of a tenant's twofa_policy. {'mode':'off'} on any error / un-run mig 711
    (→ no enforcement). Normalized to a safe shape here (independent of the router's copy)."""
    now = time.time()
    hit = _twofa_cache.get(org)
    if hit and hit[1] > now:
        return hit[0]
    policy = {"mode": "off", "required_roles": []}
    try:
        from app.core.database import get_supabase
        rows = (get_supabase().schema("storeops").table("tenants").select("twofa_policy")
                .eq("org_id", org).limit(1).execute().data) or []
        raw = rows[0].get("twofa_policy") if rows else None
        if isinstance(raw, dict):
            m = str(raw.get("mode") or "off").lower()
            policy["mode"] = m if m in ("off", "optional", "required") else "off"
            rr = raw.get("required_roles")
            if isinstance(rr, list):
                policy["required_roles"] = [str(r) for r in rr if str(r).strip()]
    except Exception:
        policy = {"mode": "off", "required_roles": []}
    _twofa_cache[org] = (policy, now + _TWOFA_TTL)
    return policy


def _tenant_needs_2fa(org: str, role, twofa_enabled) -> bool:
    """Does THIS (org, user) require 2FA? Middleware enforces tenant-wide 'required' (optionally
    role-scoped). 'optional' is user-opt-in (twofa_enabled); 'off' → never."""
    policy = _tenant_2fa_policy(org)
    mode = policy.get("mode")
    if mode == "off":
        return False
    if mode == "required":
        rr = policy.get("required_roles") or []
        return (not rr) or ((role or "") in rr)
    return bool(twofa_enabled)   # optional → only the users who turned it on


def _twofa_marker_ok(token: str, uid: str, org: str) -> bool:
    """Verify the stateless x-2fa-token for this login/org. FAILS OPEN on a verifier import/error so a
    library glitch can never lock anyone out (the safe direction under 'never strand the owner')."""
    try:
        from app.modules.core.auth_security import twofa_token_valid_for, now_ts
        return twofa_token_valid_for(token, uid, org, now_ts())
    except Exception:
        return True


async def _reject_2fa(send):
    body = b'{"detail":"two-factor authentication required","code":"2fa_required"}'
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def _is_public(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    for p in _PUBLIC_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return True
    if path.endswith(_RUN_DUE_SUFFIX):
        return True
    return False


def _fetch_memberships(client, uid):
    """Every app_users row for this auth_id, earliest first. Also selects role + twofa_enabled (used by
    the 2FA gate). Tolerant of post-706/711 columns being un-run: falls back through progressively
    leaner column lists so a missing column never breaks identity resolution (pre-706 there is at most
    one row anyway; a missing twofa_enabled just means 2FA-off)."""
    tbl = client.schema("storeops").table("app_users")
    for cols in ("org_id,super_admin,is_default_org,role,twofa_enabled",
                 "org_id,super_admin,is_default_org,role",
                 "org_id,super_admin,is_default_org"):
        try:
            return (tbl.select(cols).eq("auth_id", uid).order("created_at").execute().data) or []
        except Exception:
            continue
    try:
        return (tbl.select("org_id,super_admin").eq("auth_id", uid).execute().data) or []
    except Exception:
        return []


def _resolve_identity(token: str):
    """Verify the Supabase JWT and resolve the login's MEMBERSHIP set. Cached (positive only).

    Returns (authenticated, super_admin, member_orgs, default_org, org_info, uid):
      • org_info = {org_id: {"role","twofa_enabled"}} for the 2FA gate (additive; empty pre-migration).
      • uid = the auth account id (for verifying the 2FA marker binds to this login).
    The first four elements are UNCHANGED in meaning from before:
      (True,  True,  (...),      _,   …) — verified SUPER-ADMIN → NO rewrite, client org_id honored.
      (True,  False, (orgs...),  org, …) — verified normal user → rewrite org_id to the ACTIVE org.
      (True,  False, (),         None,…) — verified user with NO app_users row → no rewrite.
      (False, False, (),         None,…) — token missing/expired/unverifiable → caller must REJECT.

    Blocking — call via to_thread. Negative results are NOT cached, so a transient Supabase hiccup
    never pins a good user out for the TTL, and an expired token re-checks on refresh."""
    now = time.time()
    hit = _cache.get(token)
    if hit and hit[1] > now:
        return hit[0]
    try:
        from app.core.database import get_supabase_admin, get_supabase
        resp = get_supabase_admin().auth.get_user(token)
        user = getattr(resp, "user", None) or resp
        uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
        if not uid:
            return (False, False, (), None, {}, None)   # token did not resolve → not authenticated
        rows = _fetch_memberships(get_supabase(), uid)
        super_admin = any(r.get("super_admin") for r in rows)
        member_orgs = tuple(r.get("org_id") for r in rows if r.get("org_id"))
        # default membership: the row flagged is_default_org, else the earliest (rows are ordered).
        default_org = next((r.get("org_id") for r in rows if r.get("is_default_org")),
                           (member_orgs[0] if member_orgs else None))
        org_info = {r.get("org_id"): {"role": r.get("role"), "twofa_enabled": bool(r.get("twofa_enabled"))}
                    for r in rows if r.get("org_id")}
        result = (True, super_admin, member_orgs, default_org, org_info, uid)
        _cache[token] = (result, now + _TTL)
        return result
    except Exception:
        return (False, False, (), None, {}, None)   # verification error / bad token → unauthenticated


def _pick_active_org(member_orgs, default_org, requested):
    """Resolve the tenant this request acts as. `requested` is the UNTRUSTED x-active-org header:
    honored ONLY if it names a tenant the login belongs to; otherwise fall back to the default
    membership. Empty member set (unprovisioned) ⇒ None ⇒ no rewrite (client org honored)."""
    if requested and requested in member_orgs:
        return requested
    return default_org


async def _reject_401(send):
    body = b'{"detail":"authentication required"}'
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class TenantScopeMiddleware:
    """Pure ASGI middleware (reliable scope mutation). Forces org_id=<token's org> on the query
    string, and (when REQUIRE_AUTH is on) rejects unauthenticated hits to non-public routes."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not _enabled():
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if _is_public(path):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}
        auth = headers.get("authorization", "")
        token = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else ""
        ok, super_admin, member_orgs, default_org, org_info, uid = (
            (await asyncio.to_thread(_resolve_identity, token)) if token
            else (False, False, (), None, {}, None))
        if not ok:
            # No valid identity. Kill switch OFF (REQUIRE_AUTH=0) ⇒ old pass-through (client org_id
            # honored). Kill switch ON (default) ⇒ reject — no request reaches a tenant's data
            # without proving which tenant it is.
            if _require_auth():
                return await _reject_401(send)
            return await self.app(scope, receive, send)
        if super_admin:
            # Super-admin ⇒ no rewrite; the client-supplied org_id is honored (cross-tenant admin).
            return await self.app(scope, receive, send)
        # Normal login: honor the caller's chosen tenant ONLY if it is one of their memberships,
        # else fall back to their default membership. Empty membership ⇒ no rewrite (unprovisioned).
        requested = (headers.get(_ACTIVE_ORG_HEADER, "") or "").strip()
        org = _pick_active_org(member_orgs, default_org, requested)
        # 2FA gate (auth-hardening) — ADDITIVE, super-admins already returned above. Enforced only when
        # the global break-glass TWOFA_ENFORCE is on (default) AND the active tenant requires 2FA for
        # this user AND a valid x-2fa-token is absent. The OTP start/verify endpoints live under the
        # allowlisted /api/v1/core/me prefix, so a user can always obtain + submit a code. Any error /
        # un-run mig 711 → _tenant_needs_2fa is False → no-op (never a lockout). TWOFA_ENFORCE=0 kills it.
        if org and _twofa_enforce():
            info = org_info.get(org) or {}
            if _tenant_needs_2fa(org, info.get("role"), info.get("twofa_enabled")):
                if not _twofa_marker_ok(headers.get("x-2fa-token", ""), uid, org):
                    return await _reject_2fa(send)
        if org:
            qs = parse_qs(scope.get("query_string", b"").decode(), keep_blank_values=True)
            qs["org_id"] = [org]   # override any client-supplied org_id with the resolved active tenant
            scope = {**scope, "query_string": urlencode(qs, doseq=True).encode()}
        return await self.app(scope, receive, send)
