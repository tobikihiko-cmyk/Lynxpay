"""Phase 2 reliability, reconciliation, encryption rotation, and team tests."""

import asyncio
from datetime import timedelta
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.core.security import (
    decrypt_sensitive_value,
    decrypt_sensitive_values,
    encrypt_sensitive_value,
    encrypt_sensitive_values,
    encryption_key_version,
    reencrypt_sensitive_value,
)
from app.daraja import DarajaClient, DarajaSecrets, clear_daraja_token_cache
from app.models import (
    AuditLog,
    MerchantAccount,
    Payment,
    PaymentLedgerEntry,
    PaymentStatusCheck,
    TeamInvitation,
    User,
    WebhookDelivery,
    WebhookDeliveryAttempt,
    WebhookEndpoint,
)
from app.reconciliation import reconcile_payment
from app.service import transition_and_record, utcnow
from app.webhooks import (
    UnsafeWebhookUrlError,
    claim_deliveries,
    deliver_claimed,
    resolve_public_addresses,
    sign_payload,
)

BASE = "/api/v1"


def _add_successful_merchant_verification(db, merchant):
    payment = Payment(
        organization_id=merchant["organization_id"],
        merchant_account_id=merchant["id"],
        external_reference=f"PHASE2-VERIFY-{merchant['id']}",
        customer_phone="254712345678",
        amount=1,
        currency="KES",
        description="Phase 2 fixture merchant verification",
        purpose="merchant_verification",
        status="success",
        checkout_request_id=f"phase2-verify-{merchant['id']}",
        mpesa_receipt_number=f"P2-{merchant['id']}",
        result_code="0",
    )
    db.add(payment)
    db.commit()


@pytest.fixture
def merchant(client, auth_headers):
    response = client.post(
        f"{BASE}/merchants",
        headers=auth_headers,
        json={
            "merchant_name": "Phase 2 Paybill",
            "shortcode": "123456",
            "shortcode_type": "paybill",
            "environment": "sandbox",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def credential(db, client, auth_headers, merchant):
    response = client.post(
        f"{BASE}/merchants/{merchant['id']}/daraja-credentials",
        headers=auth_headers,
        json={
            "consumer_key": "consumer-key-secret",
            "consumer_secret": "consumer-secret-value",
            "passkey": "daraja-passkey-value",
            "shortcode": "123456",
            "environment": "sandbox",
        },
    )
    assert response.status_code == 201, response.text
    with patch(
        "app.daraja.DarajaClient.get_access_token", new=AsyncMock(return_value="test-oauth-token")
    ):
        tested = client.post(
            f"{BASE}/merchants/{merchant['id']}/daraja-credentials/test",
            headers=auth_headers,
        )
    assert tested.status_code == 200, tested.text
    _add_successful_merchant_verification(db, merchant)
    activated = client.patch(
        f"{BASE}/merchants/{merchant['id']}", headers=auth_headers, json={"status": "active"}
    )
    assert activated.status_code == 200, activated.text
    return response.json()


@pytest.fixture
def stk_payment(client, auth_headers, merchant, credential):
    del credential
    with patch(
        "app.daraja.DarajaClient.stk_push",
        new=AsyncMock(
            return_value=(
                {
                    "ResponseCode": "0",
                    "MerchantRequestID": "MR-PHASE2",
                    "CheckoutRequestID": "ws_CO_PHASE2",
                },
                {
                    "Password": "generated-password",
                    "PhoneNumber": "254712345678",
                    "PartyA": "254712345678",
                },
            )
        ),
    ):
        response = client.post(
            f"{BASE}/payments/stk-push",
            headers={**auth_headers, "Idempotency-Key": "phase2-payment"},
            json={
                "merchant_id": merchant["id"],
                "amount": "100",
                "phone_number": "0712345678",
                "external_reference": "PHASE2-PAYMENT",
                "description": "Phase 2 test",
            },
        )
    assert response.status_code == 201, response.text
    return response.json()


def test_envelope_encryption_rotates_without_losing_old_key(monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_ACTIVE_KEY_ID", "old")
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS_JSON", '{"old":"old-master-key"}')
    old_ciphertext = encrypt_sensitive_value("merchant-secret")
    assert encryption_key_version(old_ciphertext) == "old"
    assert decrypt_sensitive_value(old_ciphertext) == "merchant-secret"

    monkeypatch.setattr(settings, "ENCRYPTION_ACTIVE_KEY_ID", "new")
    monkeypatch.setattr(
        settings,
        "ENCRYPTION_KEYS_JSON",
        '{"old":"old-master-key","new":"new-master-key"}',
    )
    rotated = reencrypt_sensitive_value(old_ciphertext)
    assert encryption_key_version(rotated) == "new"
    assert decrypt_sensitive_value(rotated) == "merchant-secret"
    assert "merchant-secret" not in rotated


def test_aws_kms_provider_wraps_only_data_keys(monkeypatch):
    calls = []

    class FakeKms:
        def encrypt(self, **kwargs):
            calls.append(("encrypt", kwargs))
            return {"CiphertextBlob": b"kms:" + kwargs["Plaintext"]}

        def decrypt(self, **kwargs):
            calls.append(("decrypt", kwargs))
            return {"Plaintext": kwargs["CiphertextBlob"].removeprefix(b"kms:")}

    fake_kms = FakeKms()
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=lambda *_a, **_k: fake_kms))
    monkeypatch.setattr(settings, "ENCRYPTION_PROVIDER", "aws_kms")
    monkeypatch.setattr(settings, "ENCRYPTION_ACTIVE_KEY_ID", "kms-v1")
    monkeypatch.setattr(
        settings,
        "ENCRYPTION_KMS_KEY_IDS_JSON",
        '{"kms-v1":"arn:aws:kms:af-south-1:123:key/test"}',
    )
    ciphertext = encrypt_sensitive_value("daraja-credential")
    assert ciphertext.startswith("env1::kms-v1::")
    assert "daraja-credential" not in ciphertext
    assert decrypt_sensitive_value(ciphertext) == "daraja-credential"
    assert calls[0][1]["Plaintext"] != b"daraja-credential"
    assert calls[0][1]["EncryptionContext"] == {
        "service": "lynxpay",
        "key_version": "kms-v1",
    }


def test_daraja_credential_bundle_uses_one_kms_wrap_and_unwrap(monkeypatch):
    calls = []

    class FakeKms:
        def encrypt(self, **kwargs):
            calls.append("encrypt")
            return {"CiphertextBlob": b"kms:" + kwargs["Plaintext"]}

        def decrypt(self, **kwargs):
            calls.append("decrypt")
            return {"Plaintext": kwargs["CiphertextBlob"].removeprefix(b"kms:")}

    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=lambda *_a, **_k: FakeKms()))
    monkeypatch.setattr(settings, "ENCRYPTION_PROVIDER", "aws_kms")
    monkeypatch.setattr(settings, "ENCRYPTION_ACTIVE_KEY_ID", "kms-bundle-v2")
    monkeypatch.setattr(
        settings,
        "ENCRYPTION_KMS_KEY_IDS_JSON",
        '{"kms-bundle-v2":"arn:aws:kms:af-south-1:123:key/bundle-test"}',
    )
    encrypted = encrypt_sensitive_values(["consumer", "secret", "passkey", None])
    assert calls == ["encrypt"]
    assert decrypt_sensitive_values(encrypted) == ["consumer", "secret", "passkey", None]
    assert calls == ["encrypt", "decrypt"]


@pytest.mark.asyncio
async def test_daraja_oauth_cache_single_flights_concurrent_requests(monkeypatch):
    clear_daraja_token_cache()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "cached-oauth-token", "expires_in": "3599"}

    class HttpClient:
        calls = 0

        async def get(self, *_args, **_kwargs):
            self.calls += 1
            await asyncio.sleep(0)
            return Response()

    http = HttpClient()
    monkeypatch.setattr("app.daraja._shared_http_client", lambda _base_url: http)
    client = DarajaClient("sandbox")
    secrets = DarajaSecrets("cache-consumer", "cache-secret", "passkey")
    tokens = await asyncio.gather(*(client.get_access_token(secrets) for _ in range(10)))
    assert tokens == ["cached-oauth-token"] * 10
    assert http.calls == 1


def test_webhook_signature_is_deterministic_and_body_bound():
    first = sign_payload("whsec_test", 1234, b'{"event":"payment.success"}')
    second = sign_payload("whsec_test", 1234, b'{"event":"payment.failed"}')
    assert first.startswith("t=1234,v1=")
    assert first != second
    assert first == sign_payload("whsec_test", 1234, b'{"event":"payment.success"}')


@pytest.mark.asyncio
async def test_webhook_ssrf_rejects_private_dns_answers(monkeypatch):
    answer = [(2, 1, 6, "", ("127.0.0.1", 443))]
    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", AsyncMock(return_value=answer))
    with pytest.raises(UnsafeWebhookUrlError):
        await resolve_public_addresses("attacker.example", 443)


@pytest.mark.asyncio
async def test_daraja_status_query_contract_redacts_nothing_into_response():
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ResultCode": "0", "ResultDesc": "Success"}

    class HttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, **kwargs):
            captured.update(url=url, payload=kwargs["json"], headers=kwargs["headers"])
            return Response()

    client = DarajaClient("sandbox")
    with (
        patch.object(client, "get_access_token", new=AsyncMock(return_value="oauth-token")),
        patch("app.daraja.httpx.AsyncClient", return_value=HttpClient()),
    ):
        response, payload = await client.query_stk_status(
            secrets=DarajaSecrets("consumer", "secret", "passkey"),
            shortcode="123456",
            checkout_request_id="ws_QUERY_1",
        )
    assert captured["url"].endswith("/mpesa/stkpushquery/v1/query")
    assert captured["payload"]["CheckoutRequestID"] == "ws_QUERY_1"
    assert captured["headers"]["Authorization"] == "Bearer oauth-token"
    assert payload["Password"] != "passkey"
    assert response["ResultCode"] == "0"


@pytest.mark.asyncio
async def test_webhook_retry_then_dead_letters(db, merchant, monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_AUTO_PAUSE_FAILURES", 2)
    merchant_row = db.query(MerchantAccount).filter(MerchantAccount.id == merchant["id"]).one()
    endpoint = WebhookEndpoint(
        organization_id=merchant_row.organization_id,
        merchant_account_id=merchant_row.id,
        url="https://merchant.example.test/events",
        event_types=["payment.success"],
        secret_encrypted=encrypt_sensitive_value("whsec_test"),
        encryption_key_version=settings.ENCRYPTION_ACTIVE_KEY_ID,
        status="active",
    )
    db.add(endpoint)
    db.flush()
    delivery = WebhookDelivery(
        webhook_endpoint_id=endpoint.id,
        event_type="payment.success",
        payload={"event": "payment.success"},
        status="queued",
        attempts=0,
        max_attempts=2,
        next_retry_at=utcnow(),
    )
    db.add(delivery)
    db.commit()
    monkeypatch.setattr("app.webhooks.send_webhook", AsyncMock(return_value=(503, "unavailable")))

    assert claim_deliveries(db, "worker-1") == [delivery.id]
    first = await deliver_claimed(db, delivery.id, "worker-1")
    assert first.status == "retry_scheduled"
    assert first.attempts == 1
    assert first.response_status_code == 503

    first.next_retry_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert claim_deliveries(db, "worker-2") == [delivery.id]
    second = await deliver_claimed(db, delivery.id, "worker-2")
    assert second.status == "dead_letter"
    assert second.attempts == 2
    assert second.lease_owner is None
    attempts = (
        db.query(WebhookDeliveryAttempt)
        .filter_by(webhook_delivery_id=delivery.id)
        .order_by(WebhookDeliveryAttempt.attempt_number)
        .all()
    )
    assert [attempt.status for attempt in attempts] == ["failed", "failed"]
    assert [attempt.response_status_code for attempt in attempts] == [503, 503]
    db.refresh(endpoint)
    assert endpoint.status == "paused"
    assert endpoint.pause_reason == "consecutive_delivery_failures"
    assert db.query(AuditLog).filter_by(action="webhook_endpoint_auto_paused").count() == 1


def test_webhook_claims_are_fair_across_endpoints(db, merchant, monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_CLAIM_PER_ENDPOINT", 2)
    merchant_row = db.query(MerchantAccount).filter(MerchantAccount.id == merchant["id"]).one()
    endpoints = []
    for suffix in ("busy", "quiet"):
        endpoint = WebhookEndpoint(
            organization_id=merchant_row.organization_id,
            merchant_account_id=merchant_row.id,
            url=f"https://{suffix}.example.test/events",
            event_types=["payment.success"],
            secret_encrypted=encrypt_sensitive_value(f"whsec_{suffix}"),
            encryption_key_version=settings.ENCRYPTION_ACTIVE_KEY_ID,
            status="active",
        )
        db.add(endpoint)
        endpoints.append(endpoint)
    db.flush()
    for index in range(10):
        db.add(
            WebhookDelivery(
                webhook_endpoint_id=endpoints[0].id,
                event_type="payment.success",
                payload={"index": index},
                status="queued",
                next_retry_at=utcnow() - timedelta(seconds=20 - index),
            )
        )
    quiet = WebhookDelivery(
        webhook_endpoint_id=endpoints[1].id,
        event_type="payment.success",
        payload={"quiet": True},
        status="queued",
        next_retry_at=utcnow() - timedelta(seconds=1),
    )
    db.add(quiet)
    db.commit()

    claimed = claim_deliveries(db, "fair-worker", limit=3)
    rows = db.query(WebhookDelivery).filter(WebhookDelivery.id.in_(claimed)).all()
    assert len(claimed) == 3
    assert {row.webhook_endpoint_id for row in rows} == {endpoints[0].id, endpoints[1].id}
    assert quiet.id in claimed


@pytest.mark.asyncio
async def test_reconciliation_verified_success_updates_payment_and_audits(db, stk_payment):
    with patch(
        "app.reconciliation.DarajaClient.query_stk_status",
        new=AsyncMock(
            return_value=(
                {"ResultCode": "0", "ResultDesc": "The service request is processed successfully."},
                {"Password": "must-not-be-stored"},
            )
        ),
    ):
        check = await reconcile_payment(db, stk_payment["id"])

    payment = db.query(Payment).filter(Payment.id == stk_payment["id"]).one()
    assert check.outcome == "success"
    assert payment.status == "success"
    assert payment.paid_at is not None
    assert db.query(PaymentStatusCheck).filter_by(payment_id=payment.id).count() == 1
    assert (
        db.query(PaymentLedgerEntry)
        .filter_by(payment_id=payment.id, event_type="payment.success")
        .count()
        == 1
    )
    assert db.query(AuditLog).filter_by(action="payment_status_reconciled").count() == 1
    assert "must-not-be-stored" not in str(check.raw_response)


@pytest.mark.asyncio
async def test_reconciliation_verified_failure_updates_payment(db, stk_payment):
    with patch(
        "app.reconciliation.DarajaClient.query_stk_status",
        new=AsyncMock(return_value=({"ResultCode": "1032", "ResultDesc": "Cancelled"}, {})),
    ):
        check = await reconcile_payment(db, stk_payment["id"])
    payment = db.query(Payment).filter(Payment.id == stk_payment["id"]).one()
    assert check.outcome == "failed"
    assert payment.status == "failed"
    assert payment.result_code == "1032"


def test_callback_enriches_receipt_after_reconciled_success(
    db, client, merchant, stk_payment, monkeypatch
):
    payment = db.query(Payment).filter_by(id=stk_payment["id"]).one()
    transition_and_record(
        db,
        payment=payment,
        target="success",
        event_type="payment.success",
        details={"source": "test_status_query"},
    )
    payment.result_code = "0"
    payment.paid_at = utcnow()
    db.commit()
    monkeypatch.setattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", "")
    response = client.post(
        f"{BASE}/callbacks/mpesa/{merchant['id']}",
        json={
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": "MR-PHASE2",
                    "CheckoutRequestID": "ws_CO_PHASE2",
                    "ResultCode": 0,
                    "ResultDesc": "Success",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": 100},
                            {"Name": "MpesaReceiptNumber", "Value": "PHASE2RCP"},
                            {"Name": "PhoneNumber", "Value": 254712345678},
                        ]
                    },
                }
            }
        },
    )
    assert response.status_code == 200
    db.refresh(payment)
    assert payment.status == "success"
    assert payment.mpesa_receipt_number == "PHASE2RCP"
    assert db.query(AuditLog).filter_by(action="payment_success_evidence_enriched").count() == 1


def test_team_invitation_token_is_hashed_and_acceptance_creates_member(db, client, auth_headers):
    response = client.post(
        f"{BASE}/team/invitations",
        headers=auth_headers,
        json={"email": "developer@example.co.ke", "role": "admin"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["invitation_token"]
    invitation = db.query(TeamInvitation).filter_by(id=response.json()["id"]).one()
    assert invitation.token_hash != token
    assert token not in str(invitation.__dict__)

    accepted = client.post(
        f"{BASE}/auth/invitations/{token}/accept",
        json={"full_name": "LynxPay Developer", "password": "a-strong-member-password"},
    )
    assert accepted.status_code == 201, accepted.text
    member = db.query(User).filter_by(email="developer@example.co.ke").one()
    assert member.organization_id == invitation.organization_id
    assert member.role == "admin"
    db.refresh(invitation)
    assert invitation.status == "accepted"
