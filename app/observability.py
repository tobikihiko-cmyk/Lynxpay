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
from app.core.deps import get_client_ip, ip_in_cidrs

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
PAYMENTS_PENDING_COUNT = Gauge("lynxpay_payments_pending_count", "Pending payments")
PAYMENTS_UNKNOWN_COUNT = Gauge("lynxpay_payments_unknown_count", "Unknown payments")
OLDEST_UNKNOWN_PAYMENT_AGE = Gauge(
    "lynxpay_oldest_unknown_payment_age_seconds",
    "Age of the oldest payment in unknown state",
)
WORKER_HEARTBEAT_AGE = Gauge(
    "lynxpay_worker_heartbeat_age_seconds", "Age of the freshest durable worker heartbeat"
)
WORKER_MODE_HEARTBEAT_AGE = Gauge(
    "lynxpay_worker_mode_heartbeat_age_seconds",
    "Age of the freshest durable worker heartbeat by mode",
    ["mode"],
)
CALLBACKS_STATE = Gauge("lynxpay_callbacks", "M-PESA callbacks by processing state", ["status"])
RECONCILIATION_BACKLOG = Gauge(
    "lynxpay_reconciliation_backlog", "Payments currently eligible for reconciliation"
)
OLDEST_RECONCILIATION_AGE = Gauge(
    "lynxpay_oldest_reconciliation_age_seconds",
    "Age of the oldest payment eligible for reconciliation",
)
STALE_STK_SUBMISSIONS = Gauge(
    "lynxpay_stale_stk_submissions", "STK attempts stuck in submitting state"
)
WEBHOOK_ENDPOINTS_PAUSED = Gauge(
    "lynxpay_webhook_endpoints_paused", "Webhook endpoints paused after repeated failures"
)
OLDEST_WEBHOOK_AGE = Gauge(
    "lynxpay_oldest_webhook_age_seconds", "Age of the oldest undelivered webhook"
)
DATABASE_POOL_CHECKED_OUT = Gauge(
    "lynxpay_database_pool_checked_out", "Checked-out connections in the metrics database pool"
)
DATABASE_POOL_CAPACITY = Gauge(
    "lynxpay_database_pool_capacity",
    "Configured persistent plus overflow connection capacity",
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
MERCHANT_PAYMENT_OUTCOMES = Counter(
    "lynxpay_merchant_payment_outcomes",
    "Payment outcomes by merchant for initial pilot operations",
    ["merchant_id", "status", "source"],
)
MPESA_RESULT_CODES = Counter(
    "lynxpay_mpesa_result_codes",
    "Safaricom response and result codes by operation",
    ["operation", "code"],
)
CALLBACK_LATENCY = Histogram(
    "lynxpay_callback_latency_seconds",
    "Elapsed time from payment creation to provider callback receipt",
    ["outcome"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 900, 3600),
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
DARAJA_TOKEN_CACHE = Counter(
    "lynxpay_daraja_token_cache_total", "Daraja OAuth token cache outcomes", ["outcome"]
)
REVERSAL_SUBMISSIONS = Counter(
    "lynxpay_reversal_submissions_total",
    "M-PESA reversal submission outcomes",
    ["outcome"],
)
REVERSALS_STATE = Gauge(
    "lynxpay_reversal_requests",
    "M-PESA reversal requests by state",
    ["status"],
)

LOGGER = logging.getLogger("lynxpay.requests")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,100}$")


def refresh_database_gauges(db) -> None:
    """Refresh bounded, low-cardinality queue and payment state metrics."""

    from app.models import (
        EmailOutbox,
        MpesaCallback,
        Payment,
        PaymentAttempt,
        ReversalRequest,
        WebhookDelivery,
        WebhookEndpoint,
        WorkerHeartbeat,
    )

    groups = (
        (WEBHOOK_QUEUE, WebhookDelivery),
        (EMAIL_QUEUE, EmailOutbox),
        (PAYMENTS_STATE, Payment),
        (REVERSALS_STATE, ReversalRequest),
    )
    try:
        for gauge, model in groups:
            rows = db.query(model.status, func.count(model.id)).group_by(model.status).all()
            gauge.clear()
            for status, count in rows:
                gauge.labels(str(status)).set(count)
        PAYMENTS_PENDING_COUNT.set(
            db.query(Payment).filter(Payment.status.in_(["created", "pending", "stk_sent"])).count()
        )
        PAYMENTS_UNKNOWN_COUNT.set(db.query(Payment).filter(Payment.status == "unknown").count())
        oldest_unknown = (
            db.query(func.min(Payment.created_at)).filter(Payment.status == "unknown").scalar()
        )
        OLDEST_UNKNOWN_PAYMENT_AGE.set(
            max(time.time() - oldest_unknown.timestamp(), 0) if oldest_unknown else 0
        )
        freshest = db.query(func.max(WorkerHeartbeat.last_seen_at)).scalar()
        if freshest:
            WORKER_HEARTBEAT_AGE.set(max(time.time() - freshest.timestamp(), 0))
        mode_heartbeats: dict[str, float] = {}
        for heartbeat in db.query(WorkerHeartbeat).all():
            mode = str((heartbeat.metadata_json or {}).get("mode") or "unknown")
            timestamp = heartbeat.last_seen_at.timestamp()
            mode_heartbeats[mode] = max(mode_heartbeats.get(mode, 0), timestamp)
        WORKER_MODE_HEARTBEAT_AGE.clear()
        for mode, timestamp in mode_heartbeats.items():
            WORKER_MODE_HEARTBEAT_AGE.labels(mode).set(max(time.time() - timestamp, 0))
        callback_rows = (
            db.query(MpesaCallback.processing_status, func.count(MpesaCallback.id))
            .group_by(MpesaCallback.processing_status)
            .all()
        )
        CALLBACKS_STATE.clear()
        for callback_status, count in callback_rows:
            CALLBACKS_STATE.labels(str(callback_status)).set(count)
        RECONCILIATION_BACKLOG.set(
            db.query(Payment).filter(Payment.status.in_(["stk_sent", "unknown"])).count()
        )
        oldest_reconciliation = (
            db.query(func.min(Payment.created_at))
            .filter(Payment.status.in_(["stk_sent", "unknown"]))
            .scalar()
        )
        OLDEST_RECONCILIATION_AGE.set(
            max(time.time() - oldest_reconciliation.timestamp(), 0) if oldest_reconciliation else 0
        )
        stale_cutoff = time.time() - settings.STK_SUBMITTING_TIMEOUT_SECONDS
        STALE_STK_SUBMISSIONS.set(
            db.query(PaymentAttempt)
            .filter(
                PaymentAttempt.status == "submitting",
                func.extract("epoch", PaymentAttempt.submission_started_at) <= stale_cutoff,
            )
            .count()
        )
        WEBHOOK_ENDPOINTS_PAUSED.set(
            db.query(WebhookEndpoint).filter(WebhookEndpoint.status == "paused").count()
        )
        oldest_webhook = (
            db.query(func.min(WebhookDelivery.created_at))
            .filter(WebhookDelivery.status.in_(["queued", "retry_scheduled", "delivering"]))
            .scalar()
        )
        OLDEST_WEBHOOK_AGE.set(
            max(time.time() - oldest_webhook.timestamp(), 0) if oldest_webhook else 0
        )
        pool = getattr(db.get_bind(), "pool", None)
        checkedout = getattr(pool, "checkedout", None)
        if callable(checkedout):
            DATABASE_POOL_CHECKED_OUT.set(checkedout())
        size = getattr(pool, "size", None)
        overflow = getattr(pool, "_max_overflow", 0)
        if callable(size):
            DATABASE_POOL_CAPACITY.set(max(size() + max(int(overflow), 0), 1))
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

    @staticmethod
    def _callback_budget(request: Request) -> tuple[str, int, str]:
        merchant_id = request.url.path.removeprefix("/api/v1/callbacks/mpesa/").split("/", 1)[0]
        source_ip = get_client_ip(request)
        verified_source = settings.MPESA_CALLBACK_VERIFY_MODE == "ip_allowlist" and ip_in_cidrs(
            source_ip, settings.MPESA_CALLBACK_IP_ALLOWLIST
        )
        if verified_source:
            request_class = "callback_verified"
            limit = settings.RATE_LIMIT_CALLBACK_VERIFIED_REQUESTS_PER_MINUTE
        else:
            # Malformed and unverified traffic share a deliberately small
            # ingress budget; outcome-specific callback metrics distinguish
            # malformed payloads after the raw-first durability boundary.
            request_class = "callback_unverified_or_malformed"
            limit = settings.RATE_LIMIT_CALLBACK_UNVERIFIED_REQUESTS_PER_MINUTE
        identity = hashlib.sha256(f"{merchant_id}:{source_ip}".encode()).hexdigest()[:32]
        return request_class, limit, identity

    async def dispatch(self, request: Request, call_next):
        if not settings.RATE_LIMIT_ENABLED or request.url.path in {"/health", "/ready", "/metrics"}:
            return await call_next(request)
        if request.url.path.startswith("/api/v1/auth/"):
            request_class = "auth"
            limit = settings.RATE_LIMIT_AUTH_REQUESTS_PER_MINUTE
            identity = self._identity(request)
        elif request.url.path.startswith("/api/v1/callbacks/mpesa/"):
            request_class, limit, identity = self._callback_budget(request)
        else:
            request_class = "api"
            limit = settings.RATE_LIMIT_REQUESTS_PER_MINUTE
            identity = self._identity(request)
        window = int(time.time()) // 60
        key = f"lynxpay:rate:{request_class}:{identity}:{window}"
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
