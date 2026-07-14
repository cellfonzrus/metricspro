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
    public token endpoints; and every `*/run-due` sweep (each verifies its own x-notify-secret and
    is called by pg_cron with no JWT). Each of these authenticates by another mechanism (its own
    token/secret/handshake), so bypassing the JWT check for them is safe.
  • KILL SWITCH: env REQUIRE_AUTH (default ON when unset). Set REQUIRE_AUTH=0 to revert to the old
    pass-through (client org_id honored on tokenless requests) via a single Railway env change — no
    code rollback. The authenticated rewrite still runs regardless.
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
)

# Self-authenticating background sweeps: EVERY route ending in "/run-due" is invoked by pg_cron with
# no JWT and verifies its own `x-notify-secret` header inside the handler. Allowlisting the suffix
# lets the cron reach the handler (which still 403s on a wrong/absent secret) and is robust to new
# sweeps added by other modules. The full current set is enumerated in the platform-core handoff.
_RUN_DUE_SUFFIX = "/run-due"

_cache: dict = {}   # token -> ((authenticated, org_id), expiry_epoch)  — positive results only
_TTL = 60.0


def _enabled() -> bool:
    return os.environ.get("MULTI_TENANT_ENFORCE", "").lower() in ("1", "true", "yes")


def _require_auth() -> bool:
    """Kill switch. Default ON when unset; REQUIRE_AUTH=0/false/no/off reverts to old pass-through."""
    return os.environ.get("REQUIRE_AUTH", "1").lower() not in ("0", "false", "no", "off")


def _is_public(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    for p in _PUBLIC_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return True
    if path.endswith(_RUN_DUE_SUFFIX):
        return True
    return False


def _resolve_token(token: str):
    """Verify the Supabase JWT and resolve (authenticated, org_id). Cached (positive only).

    Returns:
      (True,  "<uuid>") — verified normal user → REWRITE org_id to this tenant.
      (True,  None)     — verified SUPER-ADMIN (or a verified user with no app_users row yet) → NO
                          rewrite, client-supplied org_id honored (this is what makes cross-tenant
                          admin work — behavior UNCHANGED from before this hardening).
      (False, None)     — token missing/expired/unverifiable → caller must REJECT (401).

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
            return (False, None)   # token did not resolve to a user → not authenticated
        org = None
        rows = (get_supabase().schema("storeops").table("app_users")
                .select("org_id,super_admin").eq("auth_id", uid).limit(1).execute().data) or []
        if rows and not rows[0].get("super_admin"):
            org = rows[0].get("org_id")
        # super-admin (or no app_users row) → org stays None ⇒ no rewrite (query org_id honored)
        result = (True, org)
        _cache[token] = (result, now + _TTL)
        return result
    except Exception:
        return (False, None)   # verification error / bad token → treat as unauthenticated


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
        ok, org = (await asyncio.to_thread(_resolve_token, token)) if token else (False, None)
        if not ok:
            # No valid identity. Kill switch OFF (REQUIRE_AUTH=0) ⇒ old pass-through (client org_id
            # honored). Kill switch ON (default) ⇒ reject — no request reaches a tenant's data
            # without proving which tenant it is.
            if _require_auth():
                return await _reject_401(send)
            return await self.app(scope, receive, send)
        if org:
            qs = parse_qs(scope.get("query_string", b"").decode(), keep_blank_values=True)
            qs["org_id"] = [org]   # override any client-supplied org_id with the authenticated tenant
            scope = {**scope, "query_string": urlencode(qs, doseq=True).encode()}
        return await self.app(scope, receive, send)
