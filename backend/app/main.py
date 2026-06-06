"""MetricsPro Platform API — FastAPI main entry point"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.modules.commcalc.router import router as commcalc_router
from app.modules.storeops.router import router as storeops_router

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

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0", "modules": ["commcalc", "storeops"]}
