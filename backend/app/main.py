"""MetricsPro Platform API — FastAPI main entry point"""
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


_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0", "modules": ["commcalc", "storeops", "notify", "core", "account", "storevisit", "closing", "helpdesk", "hr"]}
