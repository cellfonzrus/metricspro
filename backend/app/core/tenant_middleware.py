"""Tenant-scope middleware (SaaS P3 hardening) — GATED OFF by default.

When env MULTI_TENANT_ENFORCE=1, every request's org_id is DERIVED FROM THE VERIFIED TOKEN, not
trusted from the query string: the middleware verifies the Supabase bearer token, resolves the
caller's storeops.app_users.org_id, and REWRITES the org_id query param to that value — so a user
can't reach another tenant by passing a different org_id (the residual risk left by P2's client-side
scoping). Public routes are allowlisted; token→org_id is cached briefly to avoid a Supabase auth
call per request. Default OFF ⇒ pure pass-through, zero behavior change. Enable only AFTER the
org_id leak fixes are complete and the cross-tenant isolation test passes.
"""
import os
import time
import asyncio
from urllib.parse import parse_qs, urlencode

_PUBLIC_PREFIXES = (
    "/health", "/docs", "/redoc", "/openapi.json",
    "/api/v1/core/me", "/api/v1/core/auth-config", "/api/v1/core/signup",
    "/api/v1/remediation/whatsapp-webhook",  # Meta WhatsApp calls this with no bearer token
)
_cache: dict = {}   # token -> (org_id, expiry_epoch)
_TTL = 60.0


def _enabled() -> bool:
    return os.environ.get("MULTI_TENANT_ENFORCE", "").lower() in ("1", "true", "yes")


def _org_for_token(token: str):
    """Verify the Supabase JWT and resolve the caller's org_id (cached). Returns None when the caller is
    a SUPER-ADMIN — the middleware then leaves the client-supplied org_id intact, so cross-tenant
    management (list_users/list_employees/roles/assign for another tenant via ?org_id=…) keeps working.
    Blocking — call via to_thread."""
    now = time.time()
    hit = _cache.get(token)
    if hit and hit[1] > now:
        return hit[0]
    try:
        from app.core.database import get_supabase_admin, get_supabase
        resp = get_supabase_admin().auth.get_user(token)
        user = getattr(resp, "user", None) or resp
        uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
        org = None
        if uid:
            rows = (get_supabase().schema("storeops").table("app_users")
                    .select("org_id,super_admin").eq("auth_id", uid).limit(1).execute().data) or []
            if rows and not rows[0].get("super_admin"):
                org = rows[0].get("org_id")
            # super-admin (or no app_users row) → org stays None ⇒ no rewrite (query org_id honored)
        _cache[token] = (org, now + _TTL)
        return org
    except Exception:
        return None


class TenantScopeMiddleware:
    """Pure ASGI middleware (reliable scope mutation). Forces org_id=<token's org> on the query string."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not _enabled():
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
            org = await asyncio.to_thread(_org_for_token, token)
            if org:
                qs = parse_qs(scope.get("query_string", b"").decode(), keep_blank_values=True)
                qs["org_id"] = [org]   # override any client-supplied org_id with the authenticated tenant
                scope = {**scope, "query_string": urlencode(qs, doseq=True).encode()}
        return await self.app(scope, receive, send)
