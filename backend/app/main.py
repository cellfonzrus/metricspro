"""MetricsPro Platform API — FastAPI main entry point"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.modules.commcalc.router import router as commcalc_router
from app.modules.storeops.router import router as storeops_router
from app.modules.asset.router import router as asset_router
from app.modules.notify.router import router as notify_router
from app.modules.core.router import router as core_router
from app.modules.account.router import router as account_router
from app.modules.storevisit.router import router as storevisit_router

app = FastAPI(
    title="MetricsPro Platform API",
    description="Commission Intelligence & Business Operations for Cellular Services",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

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

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0", "modules": ["commcalc", "storeops", "notify", "core", "account", "storevisit"]}
