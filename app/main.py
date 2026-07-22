"""Standalone LynxPay FastAPI application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis
from sqlalchemy import text

from app import admin, auth, team
from app.core.config import settings
from app.daraja import close_daraja_clients
from app.database import MetricsSessionLocal, admin_engine, engine, metrics_engine
from app.database_roles import validate_runtime_database_role
from app.observability import (
    MetricsMiddleware,
    RedisRateLimitMiddleware,
    RequestContextMiddleware,
    configure_tracing,
    refresh_database_gauges,
)
from app.router import router as lynxpay_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.validate_runtime()
    if settings.is_production and settings.PROCESS_TYPE.strip().lower() != "api":
        raise RuntimeError("The API process requires PROCESS_TYPE=api")
    validate_runtime_database_role(engine, "API")
    validate_runtime_database_role(admin_engine, "platform-admin")
    if settings.METRICS_ENABLED:
        validate_runtime_database_role(metrics_engine, "metrics")
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    if settings.RATE_LIMIT_ENABLED:
        redis = Redis.from_url(settings.REDIS_URL)
        try:
            await redis.ping()
        finally:
            await redis.aclose()
    try:
        yield
    finally:
        await close_daraja_clients()


app = FastAPI(
    title="LynxPay API",
    version=settings.APP_VERSION,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-API-Key"],
)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(RedisRateLimitMiddleware)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(team.public_router, prefix="/api/v1")
app.include_router(team.router, prefix="/api/v1")
app.include_router(lynxpay_router, prefix="/api/v1")
configure_tracing(app, engine)


@app.get("/health")
def health():
    return {"status": "ok", "service": "lynxpay", "version": settings.APP_VERSION}


@app.get("/ready")
def ready():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False)
def metrics(request: Request):
    if not settings.METRICS_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
    if settings.METRICS_BEARER_TOKEN:
        authorization = request.headers.get("authorization", "")
        if authorization != f"Bearer {settings.METRICS_BEARER_TOKEN}":
            raise HTTPException(status_code=401, detail="Metrics authentication required")
    with MetricsSessionLocal() as db:
        refresh_database_gauges(db)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
