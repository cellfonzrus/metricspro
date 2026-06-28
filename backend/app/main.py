"""MetricsPro Platform API — FastAPI main entry point"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from app.modules.commcalc.router import router as commcalc_router
from app.modules.storeops.router import router as storeops_router
from app.modules.asset.router import router as asset_router
from app.modules.notify.router import router as notify_router
from app.modules.core.router import router as core_router
from app.modules.account.router import router as account_router
from app.modules.storevisit.router import router as storevisit_router
from app.modules.closing.router import router as closing_router
from app.modules.helpdesk.router import router as helpdesk_router

app = FastAPI(
    title="MetricsPro Platform API",
    description="Commission Intelligence & Business Operations for Cellular Services",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# gzip every response over ~1KB — JSON compresses ~10x, so big payloads (e.g. the 5MB flags list)
# transfer far faster. System-wide latency win, zero behavior change.
app.add_middleware(GZipMiddleware, minimum_size=1024)

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

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0", "modules": ["commcalc", "storeops", "notify", "core", "account", "storevisit", "closing", "helpdesk"]}
