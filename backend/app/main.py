"""MetricsPro Platform API — FastAPI main entry point"""
import os
import secrets
import traceback

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from app.core.body_limit import BodySizeLimitMiddleware
from app.core.tenant_middleware import TenantScopeMiddleware
from app.modules.commcalc.router import router as commcalc_router
from app.modules.storeops.router import router as storeops_router
from app.modules.asset.router import router as asset_router
from app.modules.notify.router import router as notify_router
from app.modules.core.router import router as core_router
from app.modules.account.router import router as account_router
from app.modules.storevisit.router import router as storevisit_router
from app.modules.closing.router import router as closing_router
from app.modules.helpdesk.router import router as helpdesk_router
from app.modules.hr.router import router as hr_router
from app.modules.billing.router import router as billing_router
from app.modules.payables.router import router as payables_router
from app.modules.remediation.router import router as remediation_router
from app.modules.recovery.router import router as recovery_router
from app.modules.pos.router import router as pos_router
from app.modules.crm.router import router as crm_router
from app.modules.referral.router import router as referral_router

app = FastAPI(
    title="MetricsPro Platform API",
    description="Commission Intelligence & Business Operations for Cellular Services",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Auth hardening (2026-07-17): masked errors + security headers (fleet-wide, steward change) ─────
# A generic message + security headers on EVERY response. Any UNHANDLED exception (a real 500) is
# converted to {"detail":"A system error occurred. Reference:<id>"} with the full traceback logged to
# core.failure_log keyed by that id — no stack trace / SQL / file path ever reaches a client.
# HTTPExceptions (intentional 4xx/5xx with a chosen detail) are handled INNER of this by Starlette's
# ExceptionMiddleware, so this only catches genuine unhandled crashes — validation/auth messages are
# untouched (directive item 5c). Best-effort logging never itself raises.
def _log_system_error(request, exc) -> str:
    ref = secrets.token_hex(4)
    try:
        from app.core.database import get_supabase
        org_id = request.query_params.get("org_id") or "00000000-0000-0000-0000-000000000001"
        get_supabase().schema("core").table("failure_log").insert({
            "org_id": org_id, "category": "system_error", "severity": "error",
            "source": f"{request.method} {request.url.path}"[:200],
            "message": f"Unhandled server error [{ref}] on {request.url.path}"[:1000],
            "detail": {"ref": ref, "method": request.method, "path": request.url.path,
                       "exc_type": type(exc).__name__,
                       "traceback": traceback.format_exc()[-4000:]},
            "remediation": ("An unexpected server error. Search core.failure_log for this reference id to "
                            "see the full trace. Fix the underlying cause; the masked message shields the "
                            "internals from the client."),
        }).execute()
    except Exception:
        pass  # mig 112 un-run / logging blocked → still return the masked message (never a stack trace)
    return ref


# HSTS (2026-08-13): tell browsers to only ever reach this API over HTTPS, platform-wide. TLS is
# terminated at the edge (Railway), so this is a single response header, not crypto — zero measurable
# cost. `includeSubDomains` scopes to the API host's own subtree; `preload` is intentionally OMITTED
# (it is an irreversible commitment to the browser preload list). Tune via env HSTS_MAX_AGE
# (default 1 year); HSTS_MAX_AGE=0 disables the header entirely (e.g. a plain-HTTP staging box).
# Harmless to native clients (they ignore it) and to plain-HTTP responses (browsers ignore HSTS
# unless it arrives over TLS), so it is safe to stamp unconditionally.
_HSTS_MAX_AGE = os.environ.get("HSTS_MAX_AGE", "31536000").strip()

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}
if _HSTS_MAX_AGE not in ("", "0"):
    _SECURITY_HEADERS["Strict-Transport-Security"] = f"max-age={_HSTS_MAX_AGE}; includeSubDomains"


class HardeningMiddleware(BaseHTTPMiddleware):
    """Masks unhandled 500s (generic message + ref, full trace to failure_log) and stamps security
    headers on every response. Placed INNER of CORS so masked responses still carry CORS headers."""
    async def dispatch(self, request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:   # genuine unhandled crash only (HTTPException handled inner)
            ref = _log_system_error(request, exc)
            response = JSONResponse(
                status_code=500,
                content={"detail": f"A system error occurred. Reference: {ref}"})
        for k, v in _SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response


# gzip every response over ~1KB — JSON compresses ~10x, so big payloads (e.g. the 5MB flags list)
# transfer far faster. System-wide latency win, zero behavior change.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# SaaS P3 hardening (GATED OFF: enable with env MULTI_TENANT_ENFORCE=1 only AFTER the org_id leak
# fixes are done + isolation test passes). Derives org_id from the verified token. Default = no-op.
app.add_middleware(TenantScopeMiddleware)

# Inbound rate limiting (Security Controls Spec §4, P0). Per-IP fixed-window limiter — strict on
# auth-sensitive paths, generous elsewhere. Added AFTER TenantScope but BEFORE AccessLog ⇒ OUTER of
# TenantScope (a flood is refused before the expensive identity/DB path) and INNER of AccessLog (a
# throttled 429 is still recorded, so a scraper still shows up in the access log). Break-glass:
# RATE_LIMIT_ENFORCE=0. Fail-open on any limiter fault.
from app.core.rate_limit import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

# System access log (owner 2026-08-16): one row per request — actor / path / status / IP / GPS. Added
# AFTER TenantScope ⇒ OUTER of it, so it sees the actor the tenant middleware resolved (via context var)
# and records the final status even on a middleware rejection (401/403). Best-effort — never blocks or
# fails a request; writes with the service-role client.
from app.core.access_log import AccessLogMiddleware
app.add_middleware(AccessLogMiddleware)

# H5 (2026-08-05 security audit): cap the request BODY. There was no limit of any kind, so one large
# POST buffered without bound into `await file.read()` → `pd.read_excel(...)` and took the single-worker
# container down for every tenant. Added AFTER TenantScope ⇒ OUTER of it, so an oversized body is
# refused before the middleware does any identity/DB work; INNER of HardeningMiddleware ⇒ the 413 still
# carries the security headers (and, being inner of CORS, the CORS headers too).
# Default 64 MB — 9.1x the largest upload this app is documented to ingest (a 7 MB full-month Sales
# workbook); env `MAX_UPLOAD_MB` tunes it, `MAX_UPLOAD_MB=0` restores the old unbounded behaviour.
app.add_middleware(BodySizeLimitMiddleware)

# Error masking + security headers — added AFTER tenant/gzip (outer of them, so it catches their
# exceptions) but BEFORE CORS (inner of CORS, so a masked 500 still gets Access-Control-Allow-Origin).
app.add_middleware(HardeningMiddleware)

# CORS (security-plan step 7.4, finding F2 in docs/PLAN_REVIEW_2026-08-09.md). This was
# `allow_origins=["*"]` together with `allow_credentials=True` — and because a browser will not accept
# a literal `*` alongside credentials, Starlette REFLECTS whatever Origin the request carries. The
# effective policy was therefore "every website on the internet is an allowed origin".
#
# Honest severity: LOW in practice, because this API authenticates with an `Authorization: Bearer`
# token read from localStorage, not with cookies — a malicious page has nothing the browser would
# attach on its behalf, so it cannot ride an existing session. But "low" is not "none", it costs
# nothing to name the origins we actually serve, and it removes the whole class before someone
# later adds cookie auth and turns it into a real one.
#
# `CORS_ORIGINS` (comma-separated) overrides the list and `CORS_ORIGIN_REGEX` the pattern, so a new
# domain is an env change, not a deploy. The default regex deliberately covers Vercel PREVIEW
# deployments (metricspro-<hash>.vercel.app) — those are real and would otherwise break on every
# branch deploy, which is exactly the kind of breakage that gets "fixed" by putting `*` back.
_cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
CORS_ORIGINS = _cors_origins or [
    "https://metricspro-five.vercel.app",   # the ONLY production app URL (metricspro.tech is email-only)
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
CORS_ORIGIN_REGEX = os.environ.get("CORS_ORIGIN_REGEX", r"https://metricspro[a-z0-9\-]*\.vercel\.app")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(commcalc_router, prefix="/api/v1")
app.include_router(storeops_router, prefix="/api/v1")
app.include_router(asset_router, prefix="/api/v1/asset")
app.include_router(notify_router, prefix="/api/v1")
app.include_router(core_router, prefix="/api/v1")
app.include_router(account_router, prefix="/api/v1")  # router carries its own /account prefix
app.include_router(storevisit_router, prefix="/api/v1")  # router carries its own /storevisit prefix
app.include_router(closing_router, prefix="/api/v1")     # router carries its own /closing prefix
app.include_router(helpdesk_router, prefix="/api/v1")    # router carries its own /helpdesk prefix
app.include_router(hr_router, prefix="/api/v1")          # router carries its own /hr prefix
app.include_router(billing_router, prefix="/api/v1")     # router carries its own /billing prefix (super-admin)
app.include_router(payables_router, prefix="/api/v1/payables")  # Device Forecasting & Vendor Payables (mig 095)
app.include_router(remediation_router, prefix="/api/v1")  # router carries its own /remediation prefix (mig 097)
app.include_router(recovery_router, prefix="/api/v1")     # Denied-Appeal Commission Recovery (mig 098)
app.include_router(pos_router, prefix="/api/v1")          # POS module — Phase 0 product catalog (mig 724)
app.include_router(crm_router, prefix="/api/v1")          # CRM — sales pipeline + Customer 360 (mig 800)
app.include_router(referral_router, prefix="/api/v1")     # Referral — QR referrals + gated commission (mig 850)

# Security posture check (Spec §2/§5): log the enforcement posture and warn on missing secrets /
# break-glass states at boot. Best-effort; STARTUP_STRICT=1 makes prod findings fail the boot.
@app.on_event("startup")
def _security_posture_startup():
    try:
        from app.core.security_posture import check_and_log
        check_and_log()
    except RuntimeError:
        raise           # STARTUP_STRICT opted into fail-to-boot
    except Exception:
        pass


# One-shot encryption backfill for operators WITHOUT shell access: set ENCRYPTION_BACKFILL_ON_BOOT=1
# and redeploy, and this seals any data that predates a field becoming encrypted (currently the carrier
# PINs in pos.customers.password). Runs in a daemon thread so it never blocks or fails the boot; each
# sweep is idempotent, so leaving the flag on across deploys/replicas is safe (clear it once done).
@app.on_event("startup")
def _encryption_backfill_startup():
    import os
    if os.environ.get("ENCRYPTION_BACKFILL_ON_BOOT", "0").strip().lower() not in ("1", "true", "yes"):
        return
    import threading

    def _work():
        import logging
        log = logging.getLogger("encryption_backfill")
        try:
            from app.core import crypto
            if not crypto.is_enabled():
                log.warning("ENCRYPTION_BACKFILL_ON_BOOT set but FIELD_ENCRYPTION_KEY is missing — skipping.")
                return
            from app.core.database import get_supabase
            from app.core.encryption_backfill import run_all
            res = run_all(get_supabase())
            log.warning("encryption backfill complete: %s (you can now clear ENCRYPTION_BACKFILL_ON_BOOT)", res)
        except Exception as e:  # never let a backfill crash the app
            log.exception("encryption backfill failed: %s", e)

    threading.Thread(target=_work, name="encryption-backfill", daemon=True).start()


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0", "modules": ["commcalc", "storeops", "notify", "core", "account", "storevisit", "closing", "helpdesk", "hr"]}
