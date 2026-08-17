"""Inbound rate limiting (Security Controls Spec §4, P0).

Nothing throttled inbound traffic before this: login attempts, exports, and blind scraping were all
unbounded. This adds a per-IP fixed-window limiter with two tiers — a strict one for auth-sensitive
paths (login / password reset / signup / 2FA) and a generous one for everything else — so a flood or a
credential-stuffing run is refused with a 429 before it reaches identity resolution or the database.

Design notes, matching the house style:
  • PURE CORE. `RateLimiter.check(key, limit, window, now)` is a pure fixed-window counter with an
    injectable clock, unit-tested below. The middleware is a thin wrapper over it.
  • IN-PROCESS. The container runs a single worker (the body-limit note in main.py documents this), so
    an in-memory counter is the whole fleet's view. No Redis dependency. Buckets self-evict.
  • KEYED BY IP. The scraping/credential-stuffing threat is per-source, and the client IP is available
    from the forwarded header BEFORE any auth/DB work — which is exactly where we want to reject a
    flood. (Per-actor limits can layer on later; per-IP is what closes the open door.)
  • FAIL OPEN. Any internal error in the limiter lets the request through — a limiter bug must never
    take the site down. The limits themselves fail CLOSED (an over-limit key is refused).
  • BREAK-GLASS. env RATE_LIMIT_ENFORCE (default ON) — set to 0/false to disable globally via a single
    Railway env change, same posture as REQUIRE_AUTH / TWOFA_ENFORCE. Limits are env-tunable so a noisy
    integration is a config change, not a deploy.

Placement (see main.py): OUTER of TenantScope (reject before the expensive identity/DB path) but INNER
of AccessLog (a throttled request is still recorded in the access log, so the scraper still shows up).
"""
import os
import time

from app.core.tenant_middleware import _client_ip_from   # reuse the exact forwarded-IP parse

# Auth-sensitive path prefixes get the strict per-IP limit. Boundary-matched (== p or startswith p+"/")
# so we never over-match a sibling route.
_AUTH_PREFIXES = (
    "/api/v1/core/auth",            # login, forgot-password, reset-password, refresh
    "/api/v1/core/signup",          # self-serve tenant signup
    "/api/v1/core/me/2fa",          # OTP start/verify
)

# Password-reset gets an even stricter cap (External Threat Defense Plan §1.2: 3/hour/IP). These are the
# reset REQUEST + completion paths; matched before the general auth tier.
_RESET_PREFIXES = (
    "/api/v1/core/auth/forgot-password",
    "/api/v1/core/auth/reset-password",
)

# Never rate-limit infrastructure/probe paths.
_SKIP_PREFIXES = ("/health", "/favicon", "/static", "/_next", "/openapi", "/docs", "/redoc")


def _enforce() -> bool:
    return os.environ.get("RATE_LIMIT_ENFORCE", "1").lower() not in ("0", "false", "no", "off")


def _int_env(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, "").strip() or default)
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _limits():
    """(general_per_min, auth_per_min, window_seconds). Generous general default so normal use is never
    touched; strict auth default to blunt credential stuffing."""
    return (_int_env("RATE_LIMIT_PER_MIN", 300),
            _int_env("RATE_LIMIT_AUTH_PER_MIN", 20),
            60)


def _reset_limit():
    """(requests, window_seconds) for password-reset paths — 3/hour/IP by default (plan §1.2)."""
    return (_int_env("RATE_LIMIT_RESET_PER_HOUR", 3), 3600)


def _match(path: str, prefixes) -> bool:
    for p in prefixes:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def _is_reset_path(path: str) -> bool:
    return _match(path, _RESET_PREFIXES)


def _is_auth_path(path: str) -> bool:
    return _match(path, _AUTH_PREFIXES)


def _skip(path: str) -> bool:
    return any(path.startswith(p) for p in _SKIP_PREFIXES)


class RateLimiter:
    """Fixed-window per-key counter. PURE given an injected `now` — no wall-clock reads inside `check`.

    State: key -> [window_start_epoch, count]. A key whose window has elapsed is reset on next touch;
    stale keys are swept opportunistically so memory can't grow without bound under a spray of distinct
    IPs.
    """
    def __init__(self, max_keys: int = 100_000):
        self._buckets: dict = {}
        self._max_keys = max_keys

    def check(self, key, limit: int, window: int, now: float):
        """Return (allowed: bool, retry_after: int). Records the hit when allowed; an over-limit hit is
        NOT counted again (so retry_after stays honest and a hammering client can't push the window)."""
        b = self._buckets.get(key)
        if b is None or now - b[0] >= window:
            self._buckets[key] = [now, 1]
            return True, 0
        if b[1] < limit:
            b[1] += 1
            return True, 0
        retry = int(window - (now - b[0])) + 1
        return False, max(retry, 1)

    def sweep(self, window: int, now: float):
        """Drop buckets whose window has fully elapsed. Called opportunistically when the map is large."""
        if len(self._buckets) < self._max_keys:
            return
        dead = [k for k, v in self._buckets.items() if now - v[0] >= window]
        for k in dead:
            self._buckets.pop(k, None)


async def _reject_403_blocked(send):
    import json as _json
    body = _json.dumps({"detail": "Access from your network has been blocked.",
                        "code": "ip_blocked"}).encode()
    await send({"type": "http.response.start", "status": 403, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]})
    await send({"type": "http.response.body", "body": body})


async def _reject_429(send, retry_after: int):
    import json as _json
    body = _json.dumps({
        "detail": "Too many requests. Please slow down and try again shortly.",
        "code": "rate_limited",
    }).encode()
    await send({"type": "http.response.start", "status": 429, "headers": [
        (b"content-type", b"application/json"),
        (b"retry-after", str(retry_after).encode()),
        (b"content-length", str(len(body)).encode()),
    ]})
    await send({"type": "http.response.body", "body": body})


class RateLimitMiddleware:
    """Pure-ASGI per-IP rate limiter. Fail-open on any internal error; limits fail-closed."""
    def __init__(self, app):
        self.app = app
        self._limiter = RateLimiter()

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not _enforce():
            return await self.app(scope, receive, send)
        try:
            path = scope.get("path", "") or ""
            method = (scope.get("method") or "GET").upper()
            if method == "OPTIONS" or _skip(path):
                return await self.app(scope, receive, send)
            headers = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}
            ip = _client_ip_from(scope, headers) or "unknown"
            # Incident containment: a super-admin-blocked IP is refused outright (mig 860). Fail-open.
            try:
                from app.core import ip_block
                if ip != "unknown" and ip_block.is_blocked(ip):
                    return await _reject_403_blocked(send)
            except Exception:
                pass
            now = time.time()
            general, auth_lim, window = _limits()
            if _is_reset_path(path):
                limit, window = _reset_limit()               # 3/hour/IP (plan §1.2)
                key = "reset:" + ip
            else:
                is_auth = _is_auth_path(path)
                limit = auth_lim if is_auth else general
                key = (("auth:" if is_auth else "gen:") + ip)
            self._limiter.sweep(window, now)
            allowed, retry = self._limiter.check(key, limit, window, now)
            if not allowed:
                return await _reject_429(send, retry)
        except Exception:
            # a limiter fault must never break the request
            return await self.app(scope, receive, send)
        return await self.app(scope, receive, send)


if __name__ == "__main__":
    # Pure-core self-tests (no server, injected clock).
    rl = RateLimiter()
    t = 1000.0
    # 3 allowed in a 60s window, 4th refused.
    assert rl.check("k", 3, 60, t) == (True, 0)
    assert rl.check("k", 3, 60, t + 1)[0] is True
    assert rl.check("k", 3, 60, t + 2)[0] is True
    allowed, retry = rl.check("k", 3, 60, t + 3)
    assert allowed is False and retry >= 1, (allowed, retry)
    # window rolls over → allowed again, counter reset.
    assert rl.check("k", 3, 60, t + 61) == (True, 0)
    # distinct keys are independent.
    assert rl.check("other", 1, 60, t + 61) == (True, 0)
    assert rl.check("other", 1, 60, t + 62)[0] is False
    # path classification.
    assert _is_auth_path("/api/v1/core/auth/reset-password") is True
    assert _is_auth_path("/api/v1/core/me/2fa/verify") is True
    assert _is_auth_path("/api/v1/commcalc/summary") is False
    assert _skip("/health") is True and _skip("/api/v1/core/me") is False
    assert _is_reset_path("/api/v1/core/auth/forgot-password") is True
    assert _is_reset_path("/api/v1/core/auth/login-precheck") is False
    print("rate_limit self-tests passed")
