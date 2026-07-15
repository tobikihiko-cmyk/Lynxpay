"""Distributed rate limiting, Prometheus metrics, and OpenTelemetry wiring."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid

from fastapi import Request
from prometheus_client import Counter, Gauge, Histogram
from redis.asyncio import Redis
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.deps import get_client_ip

HTTP_REQUESTS = Counter(
    "lynxpay_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
HTTP_DURATION = Histogram(
    "lynxpay_http_request_duration_seconds", "HTTP request latency", ["method", "route"]
)
RATE_LIMITED = Counter("lynxpay_rate_limited_total", "Rate-limited requests", ["class"])
WEBHOOK_QUEUE = Gauge("lynxpay_webhook_deliveries", "Webhook deliveries by state", ["status"])
EMAIL_QUEUE = Gauge("lynxpay_email_deliveries", "Email outbox rows by state", ["status"])
PAYMENTS_STATE = Gauge("lynxpay_payments", "Payments by state", ["status"])
WORKER_HEARTBEAT_AGE = Gauge(
    "lynxpay_worker_heartbeat_age_seconds", "Age of the freshest durable worker heartbeat"
)
DATABASE_POOL_CHECKED_OUT = Gauge(
    "lynxpay_database_pool_checked_out", "Checked-out connections in the metrics database pool"
)
DATABASE_GAUGE_ERRORS = Counter(
    "lynxpay_database_gauge_collection_errors_total",
    "Database-backed metric collection errors",
)
PAYMENTS_CREATED = Counter("lynxpay_payments_created", "Durable payment intentions created")
STK_PUSH_SENT = Counter("lynxpay_stk_push_sent", "STK Push requests accepted by Daraja")
STK_PUSH_FAILED = Counter(
    "lynxpay_stk_push_failed", "STK Push requests that failed before acceptance", ["reason"]
)
CALLBACKS_RECEIVED = Counter("lynxpay_callbacks_received", "M-PESA callbacks durably received")
CALLBACKS_PROCESSED = Counter(
    "lynxpay_callbacks_processed", "M-PESA callbacks by processing outcome", ["status"]
)
CALLBACKS_DUPLICATE = Counter("lynxpay_callbacks_duplicate", "Duplicate callbacks detected")
PAYMENT_OUTCOMES = Counter(
    "lynxpay_payment_outcomes", "Final or ambiguous payment outcomes", ["status", "source"]
)
WEBHOOK_DELIVERY_OUTCOMES = Counter(
    "lynxpay_webhook_delivery_outcomes", "Webhook delivery attempt outcomes", ["status"]
)
RECONCILIATION_CHECKS = Counter(
    "lynxpay_reconciliation_checks", "Daraja status-query outcomes", ["outcome"]
)
DARAJA_REQUEST_DURATION = Histogram(
    "lynxpay_daraja_request_duration_seconds",
    "Daraja request latency",
    ["operation", "environment"],
)

LOGGER = logging.getLogger("lynxpay.requests")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,100}$")


def refresh_database_gauges(db) -> None:
    """Refresh bounded, low-cardinality queue and payment state metrics."""

    from app.models import EmailOutbox, Payment, WebhookDelivery, WorkerHeartbeat

    groups = (
        (WEBHOOK_QUEUE, WebhookDelivery),
        (EMAIL_QUEUE, EmailOutbox),
        (PAYMENTS_STATE, Payment),
    )
    try:
        for gauge, model in groups:
            rows = db.query(model.status, func.count(model.id)).group_by(model.status).all()
            gauge.clear()
            for status, count in rows:
                gauge.labels(str(status)).set(count)
        freshest = db.query(func.max(WorkerHeartbeat.last_seen_at)).scalar()
        if freshest:
            WORKER_HEARTBEAT_AGE.set(max(time.time() - freshest.timestamp(), 0))
        pool = getattr(db.get_bind(), "pool", None)
        checkedout = getattr(pool, "checkedout", None)
        if callable(checkedout):
            DATABASE_POOL_CHECKED_OUT.set(checkedout())
    except SQLAlchemyError:
        DATABASE_GAUGE_ERRORS.inc()


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        HTTP_DURATION.labels(request.method, route).observe(time.perf_counter() - started)
        return response


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if REQUEST_ID_RE.fullmatch(supplied) else str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            LOGGER.exception(
                json.dumps(
                    {
                        "event": "request_failed",
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                    }
                )
            )
            raise
        response.headers["X-Request-ID"] = request_id
        LOGGER.info(
            json.dumps(
                {
                    "event": "request_completed",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )
        )
        return response


class RedisRateLimitMiddleware(BaseHTTPMiddleware):
    _script = """
    local current = redis.call('INCR', KEYS[1])
    if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
    return current
    """

    def __init__(self, app):
        super().__init__(app)
        self.redis = (
            Redis.from_url(settings.REDIS_URL, decode_responses=True)
            if settings.REDIS_URL
            else None
        )

    @staticmethod
    def _identity(request: Request) -> str:
        api_key = request.headers.get("x-api-key", "")
        authorization = request.headers.get("authorization", "")
        value = api_key or authorization
        if value:
            return hashlib.sha256(value.encode()).hexdigest()[:24]
        return get_client_ip(request)

    async def dispatch(self, request: Request, call_next):
        if not settings.RATE_LIMIT_ENABLED or request.url.path in {"/health", "/ready", "/metrics"}:
            return await call_next(request)
        if request.url.path.startswith("/api/v1/auth/"):
            request_class = "auth"
            limit = settings.RATE_LIMIT_AUTH_REQUESTS_PER_MINUTE
        elif request.url.path.startswith("/api/v1/callbacks/mpesa/"):
            request_class = "callback"
            limit = settings.RATE_LIMIT_CALLBACK_REQUESTS_PER_MINUTE
        else:
            request_class = "api"
            limit = settings.RATE_LIMIT_REQUESTS_PER_MINUTE
        window = int(time.time()) // 60
        key = f"lynxpay:rate:{request_class}:{self._identity(request)}:{window}"
        try:
            if not self.redis:
                raise RuntimeError("Redis is unavailable")
            current = int(await self.redis.eval(self._script, 1, key, 65))
        except Exception:
            if settings.is_production or settings.RATE_LIMIT_FAIL_CLOSED:
                return JSONResponse(
                    status_code=503, content={"detail": "Rate limiting service unavailable"}
                )
            return await call_next(request)
        if current > limit:
            RATE_LIMITED.labels(request_class).inc()
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": "60", "X-RateLimit-Limit": str(limit)},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(limit - current, 0))
        return response


def configure_tracing(app, engine) -> None:
    if not settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        return
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.OTEL_SERVICE_NAME})
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    SQLAlchemyInstrumentor().instrument(engine=engine, tracer_provider=provider)
