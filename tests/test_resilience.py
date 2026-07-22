"""Failure injection and operational security behavior."""

from datetime import timedelta

from app import worker
from app.core.config import Settings, settings
from app.email_delivery import claim_emails, deliver_claimed_email, enqueue_email
from app.models import EmailOutbox, User, WebhookDelivery, WebhookEndpoint, WorkerHeartbeat
from app.service import utcnow
from app.webhooks import claim_deliveries


def test_metrics_endpoint_requires_configured_bearer(client, monkeypatch):
    monkeypatch.setattr(settings, "METRICS_BEARER_TOKEN", "metrics-test-secret")
    assert client.get("/metrics").status_code == 401
    response = client.get("/metrics", headers={"Authorization": "Bearer metrics-test-secret"})
    assert response.status_code == 200
    assert "lynxpay_http_requests_total" in response.text
    assert "lynxpay_payments_pending_count" in response.text
    assert "lynxpay_oldest_webhook_age_seconds" in response.text
    assert "lynxpay_oldest_reconciliation_age_seconds" in response.text


def test_production_configuration_requires_distributed_rate_limiting():
    production = Settings(
        ENVIRONMENT="production",
        SECRET_KEY="x" * 32,
        PUBLIC_BASE_URL="https://pay.example.test",
        ENCRYPTION_KEYS_JSON='{"v1":"key"}',
        METRICS_ENABLED=False,
        MPESA_CALLBACK_IP_ALLOWLIST="196.201.214.200/29",
        EMAIL_DELIVERY_MODE="smtp",
        SMTP_HOST="smtp.example.test",
        RATE_LIMIT_ENABLED=False,
        REDIS_URL="",
    )
    try:
        production.validate_runtime()
    except RuntimeError as exc:
        assert "rate limiting" in str(exc)
    else:
        raise AssertionError("production accepted without distributed rate limiting")


def test_expired_webhook_lease_is_recovered(db, auth_headers):
    del auth_headers
    user = db.query(User).one()
    endpoint = WebhookEndpoint(
        organization_id=user.organization_id,
        merchant_account_id=None,
        url="https://merchant.example.test/webhook",
        event_types=["payment.success"],
        secret_encrypted="env1::test::wrapped::ciphertext",
        encryption_key_version="test",
        status="active",
    )
    db.add(endpoint)
    db.flush()
    delivery = WebhookDelivery(
        webhook_endpoint_id=endpoint.id,
        event_type="payment.success",
        payload={"event": "payment.success"},
        status="delivering",
        attempts=1,
        max_attempts=3,
        lease_owner="crashed-worker",
        lease_expires_at=utcnow() - timedelta(seconds=1),
    )
    db.add(delivery)
    db.commit()
    assert claim_deliveries(db, "replacement-worker") == [delivery.id]
    db.refresh(delivery)
    assert delivery.lease_owner == "replacement-worker"


async def test_worker_claims_network_jobs_one_at_a_time(monkeypatch):
    pending = ["job-one", "job-two", "job-three"]
    events: list[tuple] = []

    class SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    def claim(_db, worker_id, limit):
        events.append(("claim", worker_id, limit))
        return [pending.pop(0)] if pending else []

    async def process(_db, item_id, worker_id):
        events.append(("process", worker_id, item_id))

    monkeypatch.setattr(worker, "WorkerSessionLocal", SessionContext)
    processed = await worker._drain_bounded_queue("worker-one", 2, claim, process)

    assert processed == 2
    assert pending == ["job-three"]
    assert events == [
        ("claim", "worker-one", 1),
        ("process", "worker-one", "job-one"),
        ("claim", "worker-one", 1),
        ("process", "worker-one", "job-two"),
    ]


def test_worker_health_check_requires_recent_queue_heartbeat(db, monkeypatch):
    class ExistingSession:
        def __enter__(self):
            return db

        def __exit__(self, *_args):
            return False

    heartbeat = WorkerHeartbeat(
        worker_id="worker-health-test",
        hostname="worker-container",
        last_seen_at=utcnow(),
        processed_total=0,
        metadata_json={"mode": "webhooks"},
    )
    db.add(heartbeat)
    db.commit()
    monkeypatch.setattr(worker, "WorkerSessionLocal", ExistingSession)
    monkeypatch.setattr(settings, "WORKER_HEARTBEAT_MAX_AGE_SECONDS", 120)

    assert worker.worker_is_healthy("webhooks", "worker-container")
    assert not worker.worker_is_healthy("email", "worker-container")

    heartbeat.last_seen_at = utcnow() - timedelta(seconds=121)
    db.commit()
    assert not worker.worker_is_healthy("webhooks", "worker-container")


async def test_smtp_failure_is_redacted_and_retried(db, auth_headers, monkeypatch):
    del auth_headers
    user = db.query(User).one()
    record = enqueue_email(
        db,
        organization_id=user.organization_id,
        user_id=user.id,
        to_email=user.email,
        template="password_reset",
        payload={"url": "https://dashboard.example/reset?token=secret-token"},
    )
    db.commit()
    monkeypatch.setattr(settings, "EMAIL_DELIVERY_MODE", "smtp")
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.invalid")

    def fail(_record):
        raise RuntimeError("provider detail containing secret-token")

    monkeypatch.setattr("app.email_delivery._smtp_send", fail)
    assert record.id in claim_emails(db, "email-worker")
    delivered = await deliver_claimed_email(db, record.id, "email-worker")
    assert delivered.status == "retry_scheduled"
    assert delivered.last_error == "RuntimeError"
    assert "secret-token" not in str(delivered.__dict__)
    assert db.query(EmailOutbox).filter(EmailOutbox.id == record.id).count() == 1
