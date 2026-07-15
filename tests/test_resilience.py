"""Failure injection and operational security behavior."""

from datetime import timedelta

from app.core.config import Settings, settings
from app.email_delivery import claim_emails, deliver_claimed_email, enqueue_email
from app.models import EmailOutbox, User, WebhookDelivery, WebhookEndpoint
from app.service import utcnow
from app.webhooks import claim_deliveries


def test_metrics_endpoint_requires_configured_bearer(client, monkeypatch):
    monkeypatch.setattr(settings, "METRICS_BEARER_TOKEN", "metrics-test-secret")
    assert client.get("/metrics").status_code == 401
    response = client.get("/metrics", headers={"Authorization": "Bearer metrics-test-secret"})
    assert response.status_code == 200
    assert "lynxpay_http_requests_total" in response.text


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
    assert claim_emails(db, "email-worker") == [record.id]
    delivered = await deliver_claimed_email(db, record.id, "email-worker")
    assert delivered.status == "retry_scheduled"
    assert delivered.last_error == "RuntimeError"
    assert "secret-token" not in str(delivered.__dict__)
    assert db.query(EmailOutbox).count() == 1
