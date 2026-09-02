"""SERVICE_ROLE guard — keep the Chromium/Playwright browser workload off the user-facing API.

The MetricsPro backend can be deployed as a single service (today) or split into two Railway
services that share one image + database: a user-facing **API** service and a dedicated **sweeps**
worker that owns the headless-browser portal sweeps (VidaPay / epay / live-login, etc.).

This module is the switch that keeps browsers off the API service. It is **opt-IN blocking**:

    SERVICE_ROLE unset / "sweeps" / "worker" / "all" / ""  → browsers ALLOWED (zero behavior change)
    SERVICE_ROLE = "api" (or "web")                        → browsers BLOCKED

So the default single-service deploy (SERVICE_ROLE unset) is unchanged — nothing breaks. Blocking
only ever happens once the owner *explicitly* sets SERVICE_ROLE=api on the API service, which they
must do ONLY after a separate sweeps worker exists and the sweep cron is repointed at it.

Two entry points, same message:
  * require_browser_service() — raises HTTPException(503) for use at the top of browser-sweep
    ENDPOINTS, so the caller gets a clean 503 with guidance instead of a stack trace.
  * assert_browser_allowed() — raises a plain RuntimeError, for use deep in non-endpoint sweep code
    (worker threads, cron paths) where FastAPI's HTTPException is not appropriate.

TRANSPARENT PROXY (2026-09-02, VidaPay autonomous-pull restoration): once the deploy is split, the
UI still talks only to the API service — so its interactive portal-login buttons (Log in, 2FA
verify, live login) landed on the 503 above and the human flow that SEEDS the portal session became
unreachable. Setting BROWSER_SERVICE_URL on the API service (the sweeps worker's base URL) makes
require_browser_service() raise BrowserWorkProxy instead of the 503; an app-level handler in
main.py forwards the ORIGINAL request — method, path, query, auth headers, body — to the worker and
relays its response. Config, never code: unset → exactly today's 503; the worker itself never
proxies (its gate passes). The standard login+verify flow keeps its state in the data_source row
(pending_state), so any worker process can serve any step.
"""
import os

# One message, used by both the HTTP (503) and the plain-exception variants.
BLOCKED_MESSAGE = (
    "Browser/portal sweeps do not run on the user-facing API service "
    "(SERVICE_ROLE=api). Trigger this on the sweeps worker."
)

# Roles that identify the user-facing API service, where browsers must NOT launch.
_API_ROLES = {"api", "web"}


class BrowserWorkProxy(Exception):
    """Raised by require_browser_service() instead of the 503 when BROWSER_SERVICE_URL is set:
    "this request is browser work — forward it to the sweeps worker". Handled app-level in main.py
    (the handler has the Request; this module stays FastAPI-free)."""


def browser_service_url() -> str:
    """The sweeps worker's base URL for proxying browser endpoints ("" when unset)."""
    return os.environ.get("BROWSER_SERVICE_URL", "").strip().rstrip("/")


def service_role() -> str:
    """The normalized SERVICE_ROLE for this process ("" when unset)."""
    return os.environ.get("SERVICE_ROLE", "").strip().lower()


def browser_allowed() -> bool:
    """True unless SERVICE_ROLE explicitly marks this as the API/web service.

    Unset or any non-API value (sweeps / worker / all / empty / anything else) → True.
    This is opt-IN blocking: only SERVICE_ROLE=api (or web) returns False."""
    return service_role() not in _API_ROLES


def require_browser_service() -> None:
    """Endpoint guard: raise HTTPException(503) if browsers are blocked on this service.

    Call this EARLY in a browser-sweep endpoint handler so the caller receives a clean 503 with
    guidance rather than a stack trace from deep in the sweep."""
    if not browser_allowed():
        if browser_service_url():
            raise BrowserWorkProxy()
        # Imported lazily so non-endpoint sweep code can import this module without FastAPI in scope.
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=BLOCKED_MESSAGE)


def assert_browser_allowed() -> None:
    """Launch-site guard: raise RuntimeError if browsers are blocked on this service.

    Call this immediately BEFORE a Playwright/Chromium launch, deep in sweep code where an
    HTTPException would be the wrong type. This is the guaranteed choke point — no cron / run-now /
    discover / live-login / data-source path can spawn Chromium on the API service."""
    if not browser_allowed():
        raise RuntimeError(BLOCKED_MESSAGE)


def role_banner() -> str:
    """One-line startup banner, e.g. 'SERVICE_ROLE=api (browser sweeps blocked)'."""
    role = service_role() or "<unset>"
    state = "allowed" if browser_allowed() else "blocked"
    return "SERVICE_ROLE=%s (browser sweeps %s)" % (role, state)
