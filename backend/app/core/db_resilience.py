"""Connection resilience for the process-wide Supabase/PostgREST HTTP pools.

WHY THIS FILE EXISTS — the 2026-07-30 systemic incident
------------------------------------------------------
~50+ production rows in `core.failure_log` (7/17 → 7/29) share ONE root signature:

    httpx.RemoteProtocolError: Server disconnected
      … app/modules/<any>/router.py  →  postgrest/_sync/request_builder.py:51 send_with_retry
      …                              →  postgrest/base_request_builder.py:88  RequestConfig.send
    httpx.RemoteProtocolError: <ConnectionTerminated error_code:1, last_stream_id:237>   (variant)

Hit endpoints in EVERY module (core/employee-dashboard ×13, storeops/timeclock/face ×9,
helpdesk/tickets ×8, timeclock/status ×7, hr/onboarding/me ×4, storeops/stores, closing/stores,
org/my-span, time-off …). Two tell-tale patterns: SAME-SECOND multi-endpoint clusters (5 distinct
endpoints at 22:59:15 on 7/29) and quiet-time FIRST-POLL failures (kiosk at 10:16a / 10:32a).

CONFIRMED MECHANICS (read out of the INSTALLED pinned stack: supabase 2.31.0 / postgrest 2.31.0 /
httpx 0.28.1 / httpcore 1.0.9 / h2 4.3.0)
-----------------------------------------------------------------------------------------------
1. **postgrest cannot save these requests.** `send_with_retry`
   (postgrest/_sync/request_builder.py:37-56) only inspects a RETURNED `Response`:
   `should_retry()` (base_request_builder.py:99-104) requires `retry_enabled`, verb GET/"HTTP"
   (sic — a typo for HEAD, so HEAD is never retried either) AND
   `status_code in (503, 520)`. `req.send()` is a bare `self.session.request(...)` with no
   try/except anywhere on the path, so a TRANSPORT exception propagates straight out of
   `.execute()`. No postgrest/supabase config knob changes this — the fix has to live at the
   httpx layer, which is what this module is.

2. **HTTP/2 is the actual bug, not just an amplifier.** postgrest builds every one of its
   `httpx.Client`s with `http2=True` (postgrest/_sync/client.py:97-105). In httpcore 1.0.9:

     • `HTTP11Connection.has_expired()` (httpcore/_sync/http11.py:274-287) returns True when the
       keepalive window passed **OR** when the connection is IDLE and the socket
       `is_readable` — i.e. HTTP/1.1 ACTIVELY PROBES for a server-initiated FIN before handing a
       pooled connection back out, and `ConnectionPool._assign_requests_to_connections`
       (connection_pool.py:267-277) closes anything `has_expired()`.
     • `HTTP2Connection.has_expired()` (httpcore/_sync/http2.py:522-524) is
       `self._expire_at is not None and now > self._expire_at` — **NO readable probe at all.** A
       server/edge that closed or GOAWAY'd an idle h2 connection is invisible to the pool, so the
       dead connection is handed straight back out and the request dies. That is the quiet-time
       first-poll failure, verbatim.
     • `HTTP2Connection.is_available()` (http2.py:511-520) is True even while the connection is
       ACTIVE (that is what multiplexing means), so under h2 EVERY concurrent request in the
       process is assigned to the SAME socket. `_read_incoming_data` (http2.py:428-450) stores the
       first network error in `self._read_exception` and re-raises it "immediately on any future
       reads" — so one dead socket fails every in-flight request at once. That is the same-second
       multi-endpoint cluster, verbatim. `error_code:1, last_stream_id:237` also tells us that one
       socket had already carried ~118 request streams.
     • After that error the h2 connection sets `_connection_error=True` but stays ACTIVE (not
       idle, not closed, `_expire_at is None` ⇒ never `has_expired()`), so it is never pruned and
       permanently occupies a pool slot.

   ⇒ Running these pools on **HTTP/1.1** converts an invisible stale-connection reuse into a
   detected-and-pruned one, and de-amplifies a single dead socket from N failures to at most 1.

3. **No expiry value can close the race.** Even with a short `keepalive_expiry`, the peer may
   close the socket one microsecond after the pool's check. Prevention narrows the window; only a
   retry on a FRESH connection actually recovers the request. Hence both layers below.

WHAT THIS MODULE DOES
---------------------
`build_pool_client()` returns the `httpx.Client` that `app.core.database` injects into every
per-schema `SyncPostgrestClient` (postgrest supports this via its `http_client=` parameter):

  • **HTTP/1.1** (`http2=False`) so httpcore's idle-socket readable probe applies — see (2).
    Re-enable h2 for diagnosis only with `SUPABASE_HTTP2=1`.
  • Pool limits and timeouts PINNED to the values the stock stack uses today, so nothing about a
    successful request changes, and each is env-tunable:
      SUPABASE_KEEPALIVE_EXPIRY  (default 5.0s   = httpx DEFAULT_LIMITS today)
      SUPABASE_MAX_CONNECTIONS   (default 100    = httpx DEFAULT_LIMITS today)
      SUPABASE_MAX_KEEPALIVE     (default 40     = anyio's threadpool ceiling; httpx ships 20,
                                                  raised because HTTP/1.1 needs one socket per
                                                  concurrent query where h2 needed one total)
      SUPABASE_HTTP_TIMEOUT      (default 120.0s = postgrest DEFAULT_POSTGREST_CLIENT_TIMEOUT)
  • A `RetryOnDisconnectTransport` wrapper that:
      – retries **exactly once**, on a fresh pool connection, for GET/HEAD only;
      – **never** retries any other verb (POST/PATCH/PUT/DELETE — including `POST /rpc/*`, since a
        Postgres function may write; a read-only RPC can opt in by calling `.rpc(..., get=True)`,
        which makes it a real GET);
      – raises `DatabaseUnavailable` (a 503 `HTTPException`) when the request cannot be recovered,
        so the caller sees an actionable "connection lost, safe/unsafe to retry" message instead
        of today's masked 500.

DELIBERATE LIMITS
-----------------
  • The wrapper sits at `transport.handle_request`, which covers connect + request-send +
    response-HEADER read — the phase where a stale-connection reuse always fails. A disconnect
    part-way through an already-started response body is raised later, from `Response.read()`
    inside `httpx.Client.request`, and is NOT retried here: that is a connection dying mid-flight,
    a different event that a replay is not known to fix.
  • Retry set is exactly `(RemoteProtocolError, ReadError, WriteError)` — all three mean "the
    socket we were handed is dead". `ConnectError` (a FRESH connect failed = real outage) and
    every timeout are deliberately NOT retried: retrying those adds load and latency during the
    exact windows where the worker is already starved, and a timeout can mean the server IS still
    working on the request.
  • `supabase.auth` (supabase-auth) and `supabase.storage` (storage3) keep their own stock httpx
    clients. Every traceback in the incident is a postgrest `.execute()`; widening to those
    clients is a separate, separately-provable change.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Dict, Optional, Tuple

import httpx
from fastapi import HTTPException

log = logging.getLogger("app.core.db_resilience")

# ── env knobs (bootstrap-time infra, read like MULTI_TENANT_ENFORCE in tenant_middleware) ───────
DEFAULT_HTTP2 = False               # see mechanics (2): h2 has no idle-socket probe in httpcore
DEFAULT_KEEPALIVE_EXPIRY = 5.0      # httpx DEFAULT_LIMITS.keepalive_expiry today (pinned, not inherited)
DEFAULT_MAX_CONNECTIONS = 100       # httpx DEFAULT_LIMITS.max_connections today
DEFAULT_MAX_KEEPALIVE = 40          # anyio threadpool ceiling (httpx ships 20 — see docstring)
DEFAULT_TIMEOUT = 120.0             # postgrest DEFAULT_POSTGREST_CLIENT_TIMEOUT today

#: Transport exceptions that mean "the socket we were handed is dead" (see DELIBERATE LIMITS).
RETRYABLE_EXCEPTIONS: Tuple[type, ...] = (
    httpx.RemoteProtocolError,      # "Server disconnected" + h2 <ConnectionTerminated …> (GOAWAY)
    httpx.ReadError,                # ECONNRESET while reading response headers
    httpx.WriteError,               # ECONNRESET/EPIPE while writing the request
)

#: Only these verbs may be transparently replayed. Everything else may have committed server-side.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD"})


def _env(name: str) -> Optional[str]:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None


def _env_flag(name: str, default: bool) -> bool:
    v = _env(name)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    v = _env(name)
    if v is None:
        return default
    try:
        f = float(v)
    except ValueError:
        log.warning("db_resilience: %s=%r is not a number — using %s", name, v, default)
        return default
    return f if f > 0 else default


def _env_int(name: str, default: int) -> int:
    v = _env(name)
    if v is None:
        return default
    try:
        i = int(v)
    except ValueError:
        log.warning("db_resilience: %s=%r is not an int — using %s", name, v, default)
        return default
    return i if i > 0 else default


class DatabaseUnavailable(HTTPException):
    """503 — the DB connection was lost and the request could not be safely recovered.

    A subclass of `HTTPException`, so Starlette's own exception handler turns it into a clean
    503 JSON body inside a request (no `main.py` change needed, and it is NOT reported as an
    unhandled 500 crash). Outside a request (a sweep, a background task, a script) it is just a
    descriptive exception, and any of the app's best-effort `except Exception` guards still
    swallow it exactly as they swallow today's error.
    """

    def __init__(self, method: str, path: str, original: BaseException, *, retried: bool) -> None:
        self.method = method
        self.path = path
        self.original = original
        self.retried = retried
        if retried:
            msg = (
                f"Database connection was lost while reading ({method} {path}); the automatic "
                "retry on a fresh connection also failed. Nothing was changed — please retry."
            )
        else:
            msg = (
                f"Database connection was lost while sending {method} {path}. This write was NOT "
                "retried automatically because it may already have been applied — re-check the "
                "record before retrying."
            )
        super().__init__(status_code=503, detail=msg, headers={"Retry-After": "1"})


# ── process-wide counters (ops visibility; also what the proof harness asserts on) ──────────────
_stats_lock = threading.Lock()
_STATS: Dict[str, int] = {
    "requests": 0,          # requests that reached the transport
    "read_retried": 0,      # GET/HEAD replayed once after a dead-socket error
    "read_recovered": 0,    # …and the replay succeeded (a would-have-been 500, invisible to users)
    "read_retry_failed": 0,  # …and the replay ALSO failed → 503
    "write_not_retried": 0,  # non-idempotent verb hit a dead socket → 503, never replayed
}


def pool_stats() -> Dict[str, int]:
    """Snapshot of the resilience counters (safe to call from any thread)."""
    with _stats_lock:
        return dict(_STATS)


def reset_pool_stats() -> None:
    """Zero the counters. Test/diagnostic helper only."""
    with _stats_lock:
        for k in _STATS:
            _STATS[k] = 0


def _bump(key: str) -> None:
    with _stats_lock:
        _STATS[key] = _STATS.get(key, 0) + 1


class RetryOnDisconnectTransport(httpx.BaseTransport):
    """Wraps a real httpx transport; replays GET/HEAD once when the pooled socket was dead.

    Sits at the transport layer precisely because that is the layer that knows the HTTP VERB and
    owns the connection pool: the replay goes through the same pool, which by then has evicted the
    failed connection, so attempt 2 runs on a fresh socket.
    """

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self._inner = inner

    @property
    def inner(self) -> httpx.BaseTransport:
        return self._inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        _bump("requests")
        method = request.method.upper()
        # `request.url.path` deliberately excludes the query string: filters carry org_id / emails
        # and this string ends up in an error surfaced to a user and in the logs.
        path = request.url.path
        try:
            return self._inner.handle_request(request)
        except RETRYABLE_EXCEPTIONS as exc:
            if method not in IDEMPOTENT_METHODS:
                # A disconnect on a write may have committed server-side. Never replay it.
                _bump("write_not_retried")
                log.error(
                    "db_resilience: connection lost on %s %s (%s: %s) — NOT retried (write)",
                    method, path, type(exc).__name__, exc,
                )
                raise DatabaseUnavailable(method, path, exc, retried=False) from exc

            _bump("read_retried")
            log.warning(
                "db_resilience: stale pooled connection on %s %s (%s: %s) — retrying once",
                method, path, type(exc).__name__, exc,
            )
            try:
                resp = self._inner.handle_request(request)
            except RETRYABLE_EXCEPTIONS as exc2:
                _bump("read_retry_failed")
                log.error(
                    "db_resilience: retry of %s %s also failed (%s: %s)",
                    method, path, type(exc2).__name__, exc2,
                )
                raise DatabaseUnavailable(method, path, exc2, retried=True) from exc2
            _bump("read_recovered")
            return resp

    def close(self) -> None:
        self._inner.close()


def pool_limits() -> httpx.Limits:
    return httpx.Limits(
        max_connections=_env_int("SUPABASE_MAX_CONNECTIONS", DEFAULT_MAX_CONNECTIONS),
        max_keepalive_connections=_env_int("SUPABASE_MAX_KEEPALIVE", DEFAULT_MAX_KEEPALIVE),
        keepalive_expiry=_env_float("SUPABASE_KEEPALIVE_EXPIRY", DEFAULT_KEEPALIVE_EXPIRY),
    )


def http2_enabled() -> bool:
    return _env_flag("SUPABASE_HTTP2", DEFAULT_HTTP2)


def pool_timeout() -> httpx.Timeout:
    # One value for connect/read/write/pool — exactly what postgrest's `Client(timeout=120)` does
    # today. Kept identical so a slow-but-successful query cannot start failing because of us.
    return httpx.Timeout(_env_float("SUPABASE_HTTP_TIMEOUT", DEFAULT_TIMEOUT))


def resilience_enabled() -> bool:
    """False ⇒ FULL revert to the stock postgrest pool, no redeploy needed.

    Same shape as `MULTI_TENANT_ENFORCE`: this touches every module and every tenant, so it ships
    with a single-env-var escape hatch. `DB_RESILIENCE_DISABLE=1` makes `build_pool_client()` return
    None, and `app.core.database` then lets postgrest build its own client exactly as it does on
    main — including `http2=True` and the 120s timeout — i.e. today's behaviour, bug and all.
    (`SUPABASE_HTTP2=1` is the narrower knob: keep the retry wrapper, put h2 back.)
    """
    return not _env_flag("DB_RESILIENCE_DISABLE", False)


def build_pool_client(base_url: str) -> Optional[httpx.Client]:
    """Build the resilient `httpx.Client` for ONE postgrest schema pool (None ⇒ use stock).

    Mirrors postgrest's own client construction (`base_url`, `follow_redirects=True`, verify on,
    no proxy, single-value timeout) so the only differences are the ones documented above:
    HTTP/1.1, pinned/tunable limits, and the retry wrapper.
    """
    if not resilience_enabled():
        log.warning(
            "db_resilience: DB_RESILIENCE_DISABLE=1 — using the STOCK postgrest pool "
            "(http2=True, no retry). The 'Server disconnected' failure class returns."
        )
        return None
    http2 = http2_enabled()
    inner = httpx.HTTPTransport(
        verify=True,
        http1=True,
        http2=http2,
        limits=pool_limits(),
        retries=0,          # httpx default: no connect retries (unchanged)
    )
    if http2:
        log.warning(
            "db_resilience: SUPABASE_HTTP2=1 — httpcore's HTTP/2 pool has NO idle-socket probe "
            "(HTTP2Connection.has_expired), so stale-connection failures can return."
        )
    return httpx.Client(
        base_url=base_url,
        timeout=pool_timeout(),
        follow_redirects=True,
        transport=RetryOnDisconnectTransport(inner),
    )
