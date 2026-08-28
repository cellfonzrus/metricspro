"""System access log — one row per HTTP request (who · path · status · IP · GPS). Best-effort: it never
blocks the request from proceeding and never fails it if the log write errors.

It sits OUTSIDE the tenant middleware (registered after it, so it wraps it): the tenant middleware
resolves the actor and active org into context vars, then this reads them once the response is done.
GPS comes from client-sent headers (x-geo-lat / x-geo-lng / x-geo-acc) attached by the frontend API
client; when the browser hasn't granted location, GPS is simply null.
"""
import asyncio
from app.core.database import get_supabase_admin
from app.core import tenant_middleware as _tm

_SKIP_PREFIXES = ("/health", "/favicon", "/static", "/_next", "/openapi", "/docs", "/redoc")

# Strong refs to detached background writes so the event loop doesn't GC a pending task mid-flight.
_BG_WRITES = set()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _insert(row):
    try:
        get_supabase_admin().schema("core").table("access_log").insert(row).execute()
    except Exception:
        pass   # an audit write must never break a request


class AccessLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "") or ""
        method = (scope.get("method") or "GET").upper()
        if method == "OPTIONS" or any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await self.app(scope, receive, send)

        headers = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}
        _tm._set_actor(None)   # reset so a prior request's actor can never leak into an anonymous one
        status = {"code": None}

        async def _send(msg):
            if msg.get("type") == "http.response.start":
                status["code"] = msg.get("status")
            await send(msg)

        try:
            await self.app(scope, receive, _send)
        finally:
            try:
                actor = _tm._get_actor() or {}
                row = {
                    "org_id": _tm._ACTING_ORG.get(),
                    "actor_auth_id": actor.get("uid"),
                    "actor_email": actor.get("email"),
                    "actor_role": actor.get("role"),
                    "anonymous": not bool(actor.get("uid")),
                    "method": method,
                    "path": path[:400],
                    "query": (scope.get("query_string", b"").decode() or "")[:400] or None,
                    "status": status["code"],
                    "ip": (_tm._client_ip_from(scope, headers) or None),
                    "user_agent": (headers.get("user-agent") or "")[:300] or None,
                    "gps_lat": _num(headers.get("x-geo-lat")),
                    "gps_lng": _num(headers.get("x-geo-lng")),
                    "gps_accuracy_m": _num(headers.get("x-geo-acc")),
                }
                # Fire-and-forget: the audit write must never hold the response on the request path.
                # Detach it (to_thread, NOT awaited) so the response returns immediately; a strong ref
                # keeps the task alive until the write finishes.
                task = asyncio.ensure_future(asyncio.to_thread(_insert, row))
                _BG_WRITES.add(task)
                task.add_done_callback(_BG_WRITES.discard)
            except Exception:
                pass
