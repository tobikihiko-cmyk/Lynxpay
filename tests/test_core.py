"""Phase 1 contract, security, idempotency, and isolation tests."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.core.security import decrypt_sensitive_value
from app.daraja import DarajaClient, DarajaRequestNotSentError, DarajaSecrets
from app.models import (
    ApiKey,
    AuditLog,
    DarajaCredential,
    Invoice,
    MerchantAccount,
    MpesaCallback,
    Organization,
    Payment,
    PaymentLedgerEntry,
    User,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.schemas import normalize_kenyan_phone
from app.security import verify_api_key
from app.state_machine import InvalidPaymentTransitionError, ensure_transition

BASE = "/api/v1"


def _add_successful_merchant_verification(db, merchant):
    payment = Payment(
        organization_id=merchant["organization_id"],
        merchant_account_id=merchant["id"],
        external_reference=f"FIXTURE-VERIFY-{merchant['id']}",
        customer_phone="254712345678",
        amount=Decimal("1.00"),
        currency="KES",
        description="Fixture merchant verification",
        purpose="merchant_verification",
        status="success",
        checkout_request_id=f"fixture-checkout-{merchant['id']}",
        mpesa_receipt_number=f"FIXTURE-{merchant['id']}",
        result_code="0",
    )
    db.add(payment)
    db.commit()
    return payment


@pytest.fixture
def merchant(client, auth_headers):
    response = client.post(
        f"{BASE}/merchants",
        headers=auth_headers,
        json={
            "merchant_name": "Acme Paybill",
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
def api_key(client, auth_headers, merchant, credential):
    response = client.post(
        f"{BASE}/api-keys",
        headers=auth_headers,
        json={
            "name": "Test integration key",
            "merchant_id": merchant["id"],
            "scopes": ["payments:read", "payments:write", "callbacks:read", "webhooks:write"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["api_key"]


@pytest.fixture
def api_headers(api_key):
    return {"X-API-Key": api_key}


def test_payment_list_filters_merchant_verification_purpose(db, client, auth_headers, merchant):
    payments = [
        Payment(
            organization_id=merchant["organization_id"],
            merchant_account_id=merchant["id"],
            external_reference="FILTER-ORDINARY",
            customer_phone="254712345678",
            amount=Decimal("100.00"),
            currency="KES",
            description="Ordinary payment",
            purpose="payment",
            status="created",
        ),
        Payment(
            organization_id=merchant["organization_id"],
            merchant_account_id=merchant["id"],
            external_reference="FILTER-VERIFY",
            customer_phone="254712345678",
            amount=Decimal("1.00"),
            currency="KES",
            description="Onboarding verification",
            purpose="merchant_verification",
            status="success",
            checkout_request_id="filter-verification-checkout",
            mpesa_receipt_number="FILTER-RECEIPT",
            result_code="0",
        ),
    ]
    db.add_all(payments)
    db.commit()

    response = client.get(
        f"{BASE}/payments",
        headers=auth_headers,
        params={"merchant_id": merchant["id"], "purpose": "merchant_verification"},
    )

    assert response.status_code == 200, response.text
    assert [item["external_reference"] for item in response.json()["items"]] == ["FILTER-VERIFY"]
    assert response.json()["items"][0]["purpose"] == "merchant_verification"


@pytest.fixture
def stk_payment(client, api_headers, merchant):
    with patch(
        "app.daraja.DarajaClient.stk_push",
        new=AsyncMock(
            return_value=(
                {
                    "ResponseCode": "0",
                    "MerchantRequestID": "MR-100",
                    "CheckoutRequestID": "ws_CO_LYNXPAY_100",
                    "ResponseDescription": "Success. Request accepted for processing",
                },
                {
                    "Password": "sensitive-generated-password",
                    "PhoneNumber": "254712345678",
                    "PartyA": "254712345678",
                    "Amount": 100,
                    "AccountReference": "ORDER-100",
                },
            )
        ),
    ):
        response = client.post(
            f"{BASE}/payments/stk-push",
            headers={**api_headers, "Idempotency-Key": "idem-order-100"},
            json={
                "merchant_id": merchant["id"],
                "amount": "100.00",
                "phone_number": "0712345678",
                "external_reference": "ORDER-100",
                "description": "Test order 100",
            },
        )
    assert response.status_code == 201, response.text
    return response.json()


def _success_callback(
    checkout_id="ws_CO_LYNXPAY_100", receipt="SLP100ABC", amount=100, phone=254712345678
):
    return {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "MR-100",
                "CheckoutRequestID": checkout_id,
                "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": amount},
                        {"Name": "MpesaReceiptNumber", "Value": receipt},
                        {"Name": "TransactionDate", "Value": 20260715120000},
                        {"Name": "PhoneNumber", "Value": phone},
                    ]
                },
            }
        }
    }


def _failure_callback(checkout_id="ws_CO_LYNXPAY_100"):
    return {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "MR-100",
                "CheckoutRequestID": checkout_id,
                "ResultCode": 1032,
                "ResultDesc": "Request cancelled by user.",
            }
        }
    }


def test_invoice_link_collects_payment_and_marks_invoice_paid(
    db, client, auth_headers, merchant, credential, monkeypatch
):
    created = client.post(
        f"{BASE}/invoices",
        headers=auth_headers,
        json={
            "merchant_id": merchant["id"],
            "invoice_number": "LAW-2026-001",
            "client_name": "Jane Wanjiku",
            "client_phone": "0712345678",
            "service_title": "Legal consultation and filing",
            "description": "Preparation of client advisory and filing documents.",
            "amount": "1500.00",
        },
    )
    assert created.status_code == 201, created.text
    invoice = created.json()
    assert invoice["status"] == "sent"
    assert invoice["payment_link"].endswith(f"/pay/{invoice['public_id']}")

    public_invoice = client.get(f"{BASE}/public/invoices/{invoice['public_id']}")
    assert public_invoice.status_code == 200
    assert public_invoice.json()["merchant"]["name"] == "Acme Kenya Limited"
    assert public_invoice.json()["merchant"]["shortcode"] == "123456"

    with patch(
        "app.daraja.DarajaClient.stk_push",
        new=AsyncMock(
            return_value=(
                {
                    "ResponseCode": "0",
                    "MerchantRequestID": "MR-INVOICE-001",
                    "CheckoutRequestID": "ws_CO_INVOICE_001",
                    "ResponseDescription": "Success. Request accepted for processing",
                },
                {
                    "Password": "generated",
                    "PhoneNumber": "254722111222",
                    "PartyA": "254722111222",
                    "Amount": 1500,
                    "AccountReference": "LAW-2026-001-1",
                },
            )
        ),
    ):
        paid = client.post(
            f"{BASE}/public/invoices/{invoice['public_id']}/pay",
            json={"phone_number": "0722 111 222"},
        )
    assert paid.status_code == 201, paid.text
    payment = db.query(Payment).filter(Payment.invoice_id == invoice["id"]).one()
    assert payment.status == "stk_sent"
    assert payment.external_reference == "LAW-2026-001-1"
    assert payment.customer_phone == "254722111222"

    duplicate_prompt = client.post(
        f"{BASE}/public/invoices/{invoice['public_id']}/pay",
        json={"phone_number": "0722 111 222"},
    )
    assert duplicate_prompt.status_code == 409

    monkeypatch.setattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", "")
    callback = client.post(
        f"{BASE}/callbacks/mpesa/{merchant['id']}",
        json=_success_callback(
            checkout_id="ws_CO_INVOICE_001",
            receipt="INVOICE-RECEIPT-1",
            amount=1500,
            phone=254722111222,
        ),
    )
    assert callback.status_code == 200
    db.expire_all()
    stored_invoice = db.query(Invoice).filter(Invoice.id == invoice["id"]).one()
    assert stored_invoice.status == "paid"
    assert stored_invoice.payment_id == payment.id
    assert stored_invoice.paid_at is not None


def test_catalog_items_can_build_itemized_invoice(client, auth_headers, merchant, credential):
    consultation = client.post(
        f"{BASE}/catalog-items",
        headers=auth_headers,
        json={
            "merchant_id": merchant["id"],
            "item_type": "service",
            "name": "Legal consultation",
            "description": "Client advisory session",
            "unit_price": "5000.00",
        },
    )
    filing = client.post(
        f"{BASE}/catalog-items",
        headers=auth_headers,
        json={
            "merchant_id": merchant["id"],
            "item_type": "service",
            "name": "Court filing support",
            "unit_price": "2500.00",
        },
    )
    assert consultation.status_code == 201, consultation.text
    assert filing.status_code == 201, filing.text

    invoice = client.post(
        f"{BASE}/invoices",
        headers=auth_headers,
        json={
            "merchant_id": merchant["id"],
            "invoice_number": "LAW-ITEMIZED-001",
            "client_name": "Jane Wanjiku",
            "service_title": "Legal consultation and filing",
            "description": "Legal services prepared for the client.",
            "line_items": [
                {"catalog_item_id": consultation.json()["id"], "quantity": "1"},
                {"catalog_item_id": filing.json()["id"], "quantity": "2"},
            ],
        },
    )

    assert invoice.status_code == 201, invoice.text
    payload = invoice.json()
    assert payload["amount"] == "10000.00"
    assert [item["name"] for item in payload["line_items"]] == [
        "Legal consultation",
        "Court filing support",
    ]
    public_invoice = client.get(f"{BASE}/public/invoices/{payload['public_id']}")
    assert public_invoice.status_code == 200
    assert public_invoice.json()["line_items"][1]["line_total"] == "5000.00"


def test_catalog_is_limited_to_twenty_active_items(client, auth_headers, merchant, credential):
    for index in range(20):
        response = client.post(
            f"{BASE}/catalog-items",
            headers=auth_headers,
            json={
                "merchant_id": merchant["id"],
                "item_type": "service",
                "name": f"Service {index}",
                "unit_price": "100.00",
            },
        )
        assert response.status_code == 201, response.text

    rejected = client.post(
        f"{BASE}/catalog-items",
        headers=auth_headers,
        json={
            "merchant_id": merchant["id"],
            "item_type": "product",
            "name": "Extra product",
            "unit_price": "100.00",
        },
    )
    assert rejected.status_code == 409
    assert "20 active" in rejected.json()["detail"]


def test_merchant_creation_is_tenant_owned_and_audited(db, merchant):
    record = db.query(MerchantAccount).filter(MerchantAccount.id == merchant["id"]).one()
    assert record.organization_id
    assert record.status == "pending_setup"
    assert record.callback_url == f"https://lynxpay.example.test/api/v1/callbacks/mpesa/{record.id}"
    assert db.query(User).filter(User.organization_id == record.organization_id).count() == 1
    assert db.query(AuditLog).filter(AuditLog.action == "merchant_created").count() == 1


def test_native_user_login_and_me(db, client, auth_headers):
    user = db.query(User).one()
    assert user.password_hash != "correct-horse-battery-staple"

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@acme.co.ke", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["organization_id"] == user.organization_id

    me = client.get("/api/v1/auth/me", headers=auth_headers)
    assert me.status_code == 200
    assert me.json()["id"] == user.id

    rejected = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@acme.co.ke", "password": "incorrect-password"},
    )
    assert rejected.status_code == 401


def test_business_profile_is_tenant_owned_validated_and_audited(db, client, auth_headers):
    response = client.patch(
        f"{BASE}/organization",
        headers=auth_headers,
        json={
            "legal_name": "Acme Kenya Limited",
            "business_type": "Retail",
            "county": "Nairobi",
            "town": "Westlands",
            "contact_phone": "0712345678",
            "support_email": "support@acme.co.ke",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["contact_phone"] == "254712345678"
    assert response.json()["county"] == "Nairobi"
    assert client.get(f"{BASE}/organization", headers=auth_headers).json()["town"] == "Westlands"
    assert db.query(AuditLog).filter_by(action="business_profile_updated").count() == 1


def test_credential_is_encrypted_and_response_is_masked(db, credential):
    record = db.query(DarajaCredential).filter(DarajaCredential.id == credential["id"]).one()
    assert record.consumer_key_encrypted.startswith("env1::v1::")
    assert record.encryption_key_version == "v1"
    assert record.consumer_key_encrypted != "consumer-key-secret"
    assert decrypt_sensitive_value(record.consumer_key_encrypted) == "consumer-key-secret"
    assert credential["consumer_key"] == "********"
    assert "consumer-key-secret" not in str(credential)


def test_api_key_is_hashed_and_authenticates(db, client, api_key, merchant):
    record = db.query(ApiKey).filter(ApiKey.merchant_account_id == merchant["id"]).one()
    assert record.key_hash != api_key
    assert verify_api_key(api_key, record.key_hash)
    response = client.get(f"{BASE}/payments", headers={"Authorization": f"Bearer {api_key}"})
    assert response.status_code == 200


def test_api_key_environment_blocks_test_key_from_live_merchant(client, auth_headers):
    live = client.post(
        f"{BASE}/merchants",
        headers=auth_headers,
        json={
            "merchant_name": "Live merchant",
            "shortcode": "654321",
            "shortcode_type": "paybill",
            "environment": "production",
        },
    )
    assert live.status_code == 201, live.text
    key_response = client.post(
        f"{BASE}/api-keys",
        headers=auth_headers,
        json={
            "name": "Sandbox organization key",
            "environment": "sandbox",
            "scopes": ["merchants:read", "payments:read"],
        },
    )
    assert key_response.status_code == 201, key_response.text
    key = key_response.json()["api_key"]
    assert key.startswith("slp_test_")
    assert (
        client.get(f"{BASE}/merchants/{live.json()['id']}", headers={"X-API-Key": key}).status_code
        == 404
    )


def test_production_payment_write_key_must_be_merchant_bound(client, auth_headers):
    response = client.post(
        f"{BASE}/api-keys",
        headers=auth_headers,
        json={
            "name": "Unsafe live key",
            "environment": "production",
            "scopes": ["payments:write"],
        },
    )
    assert response.status_code == 422


def test_api_key_cannot_receive_credential_write_scope(client, auth_headers):
    response = client.post(
        f"{BASE}/api-keys",
        headers=auth_headers,
        json={
            "name": "Credential mutation key",
            "environment": "sandbox",
            "scopes": ["credentials:write"],
        },
    )
    assert response.status_code == 422


def test_merchant_requires_verified_credentials_before_activation(
    db, client, auth_headers, merchant
):
    created = client.post(
        f"{BASE}/merchants/{merchant['id']}/daraja-credentials",
        headers=auth_headers,
        json={
            "consumer_key": "consumer-key",
            "consumer_secret": "consumer-secret",
            "passkey": "passkey",
            "shortcode": merchant["shortcode"],
            "environment": merchant["environment"],
        },
    )
    assert created.status_code == 201
    record = db.query(MerchantAccount).filter_by(id=merchant["id"]).one()
    assert record.status == "credentials_added"
    assert (
        client.patch(
            f"{BASE}/merchants/{merchant['id']}", headers=auth_headers, json={"status": "active"}
        ).status_code
        == 409
    )
    with patch(
        "app.daraja.DarajaClient.get_access_token", new=AsyncMock(return_value="oauth-token")
    ):
        tested = client.post(
            f"{BASE}/merchants/{merchant['id']}/daraja-credentials/test",
            headers=auth_headers,
        )
    assert tested.status_code == 200
    db.refresh(record)
    assert record.status == "verified"
    assert (
        client.patch(
            f"{BASE}/merchants/{merchant['id']}", headers=auth_headers, json={"status": "active"}
        ).status_code
        == 409
    )

    with patch(
        "app.daraja.DarajaClient.stk_push",
        new=AsyncMock(
            return_value=(
                {
                    "ResponseCode": "0",
                    "MerchantRequestID": "MR-VERIFY",
                    "CheckoutRequestID": "ws_CO_VERIFY",
                },
                {"Password": "generated", "PhoneNumber": "254712345678"},
            )
        ),
    ):
        verification = client.post(
            f"{BASE}/payments/stk-push",
            headers={**auth_headers, "Idempotency-Key": "merchant-verification-lifecycle"},
            json={
                "merchant_id": merchant["id"],
                "amount": 1,
                "phone_number": "0712345678",
                "external_reference": "VERIFY-LIFECYCLE",
                "description": "Merchant verification",
                "purpose": "merchant_verification",
            },
        )
    assert verification.status_code == 201, verification.text
    assert verification.json()["purpose"] == "merchant_verification"
    assert verification.json()["status"] == "stk_sent"
    assert (
        client.patch(
            f"{BASE}/merchants/{merchant['id']}", headers=auth_headers, json={"status": "active"}
        ).status_code
        == 409
    )

    with patch.object(settings, "MPESA_CALLBACK_IP_ALLOWLIST", ""):
        callback = client.post(
            f"{BASE}/callbacks/mpesa/{merchant['id']}",
            json=_success_callback(checkout_id="ws_CO_VERIFY", receipt="VERIFY-RECEIPT", amount=1),
        )
    assert callback.status_code == 200
    activated = client.patch(
        f"{BASE}/merchants/{merchant['id']}", headers=auth_headers, json={"status": "active"}
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"


def test_merchant_verification_payment_requires_admin_verified_merchant_and_one_shilling(
    client, auth_headers, merchant
):
    wrong_amount = client.post(
        f"{BASE}/payments/stk-push",
        headers=auth_headers,
        json={
            "merchant_id": merchant["id"],
            "amount": 2,
            "phone_number": "0712345678",
            "external_reference": "INVALID-VERIFY",
            "description": "Invalid verification",
            "purpose": "merchant_verification",
        },
    )
    assert wrong_amount.status_code == 422

    not_verified = client.post(
        f"{BASE}/payments/stk-push",
        headers=auth_headers,
        json={
            "merchant_id": merchant["id"],
            "amount": 1,
            "phone_number": "0712345678",
            "external_reference": "NOT-VERIFIED",
            "description": "Invalid lifecycle",
            "purpose": "merchant_verification",
        },
    )
    assert not_verified.status_code == 409

    key_response = client.post(
        f"{BASE}/api-keys",
        headers=auth_headers,
        json={
            "name": "Untrusted verifier",
            "merchant_id": merchant["id"],
            "environment": "sandbox",
            "scopes": ["payments:write"],
        },
    )
    assert key_response.status_code == 201
    api_verification = client.post(
        f"{BASE}/payments/stk-push",
        headers={"X-API-Key": key_response.json()["api_key"]},
        json={
            "merchant_id": merchant["id"],
            "amount": 1,
            "phone_number": "0712345678",
            "external_reference": "API-VERIFY",
            "description": "API cannot verify merchant",
            "purpose": "merchant_verification",
        },
    )
    assert api_verification.status_code == 403


def test_till_merchant_requires_till_number(client, auth_headers):
    missing = client.post(
        f"{BASE}/merchants",
        headers=auth_headers,
        json={
            "merchant_name": "Till merchant",
            "shortcode": "123456",
            "shortcode_type": "till",
            "environment": "sandbox",
        },
    )
    assert missing.status_code == 422
    configured = client.post(
        f"{BASE}/merchants",
        headers=auth_headers,
        json={
            "merchant_name": "Till merchant",
            "shortcode": "123456",
            "till_number": "654321",
            "shortcode_type": "till",
            "environment": "sandbox",
        },
    )
    assert configured.status_code == 201
    assert configured.json()["till_number"] == "654321"


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("0712345678", "254712345678"),
        ("712345678", "254712345678"),
        ("254712345678", "254712345678"),
        ("+254712345678", "254712345678"),
    ],
)
def test_phone_normalization(raw, normalized):
    assert normalize_kenyan_phone(raw) == normalized


@pytest.mark.parametrize("raw", ["12345", "254612345678", "not-a-phone"])
def test_invalid_phone_is_rejected(raw):
    with pytest.raises(ValueError):
        normalize_kenyan_phone(raw)


@pytest.mark.asyncio
async def test_daraja_stk_request_uses_merchant_till_and_redacts_no_credentials():
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ResponseCode": "0", "CheckoutRequestID": "ws_CO_PAYLOAD"}

    class HttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs["json"]
            captured["headers"] = kwargs["headers"]
            return Response()

    client = DarajaClient("sandbox")
    with (
        patch.object(client, "get_access_token", new=AsyncMock(return_value="oauth-token")),
        patch("app.daraja.httpx.AsyncClient", return_value=HttpClient()),
    ):
        response, payload = await client.stk_push(
            secrets=DarajaSecrets("consumer-key", "consumer-secret", "passkey"),
            shortcode="123456",
            till_number="654321",
            shortcode_type="till",
            phone="254712345678",
            amount=Decimal("100.00"),
            external_reference="ORDER-PAYLOAD",
            description="Payload test",
            callback_url="https://pay.example.com/callback",
        )
    assert response["CheckoutRequestID"] == "ws_CO_PAYLOAD"
    assert captured["json"]["TransactionType"] == "CustomerBuyGoodsOnline"
    assert captured["json"]["BusinessShortCode"] == "123456"
    assert captured["json"]["PartyB"] == "654321"
    assert captured["json"]["Amount"] == 100
    assert captured["headers"]["Authorization"] == "Bearer oauth-token"
    assert "consumer-key" not in str(payload)
    assert "consumer-secret" not in str(payload)
    assert "passkey" not in str(payload)


def test_stk_push_creates_attempt_and_audit_without_marking_success(db, stk_payment):
    payment = db.query(Payment).filter(Payment.id == stk_payment["id"]).one()
    assert payment.status == "stk_sent"
    assert payment.customer_phone == "254712345678"
    assert payment.attempts[0].request_payload_redacted["Password"] == "********"
    assert (
        db.query(PaymentLedgerEntry).filter(PaymentLedgerEntry.payment_id == payment.id).count()
        == 3
    )
    assert db.query(AuditLog).filter(AuditLog.action == "stk_push_initiated").count() == 1


def test_stk_push_idempotency_replays_existing_payment_without_new_attempt(
    db, client, api_headers, merchant, stk_payment
):
    response = client.post(
        f"{BASE}/payments/stk-push",
        headers={**api_headers, "Idempotency-Key": "idem-order-100"},
        json={
            "merchant_id": merchant["id"],
            "amount": "100.00",
            "phone_number": "0712345678",
            "external_reference": "ORDER-100",
            "description": "Test order 100",
        },
    )
    assert response.status_code == 201
    assert response.json()["id"] == stk_payment["id"]
    assert response.json()["idempotent_replay"] is True
    assert db.query(Payment).filter(Payment.external_reference == "ORDER-100").count() == 1
    assert len(db.query(Payment).filter(Payment.id == stk_payment["id"]).one().attempts) == 1


def test_non_positive_amount_is_rejected(client, api_headers, merchant):
    response = client.post(
        f"{BASE}/payments/stk-push",
        headers=api_headers,
        json={
            "merchant_id": merchant["id"],
            "amount": "0",
            "phone_number": "0712345678",
            "external_reference": "INVALID-AMOUNT",
            "description": "Invalid",
        },
    )
    assert response.status_code == 422


def _post_stk(client, api_headers, merchant, reference):
    return client.post(
        f"{BASE}/payments/stk-push",
        headers={**api_headers, "Idempotency-Key": f"idem-{reference}"},
        json={
            "merchant_id": merchant["id"],
            "amount": "100",
            "phone_number": "0712345678",
            "external_reference": reference,
            "description": "Failure-path test",
        },
    )


def test_stk_request_not_sent_becomes_failed(db, client, api_headers, merchant, credential):
    del credential
    with patch(
        "app.daraja.DarajaClient.stk_push",
        new=AsyncMock(side_effect=DarajaRequestNotSentError("oauth failed")),
    ):
        response = _post_stk(client, api_headers, merchant, "NOT-SENT")
    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    payment = db.query(Payment).filter_by(external_reference="NOT-SENT").one()
    assert payment.attempts[0].status == "not_sent"
    assert (
        db.query(PaymentLedgerEntry)
        .filter_by(payment_id=payment.id, event_type="payment.failed")
        .count()
        == 1
    )


def test_stk_uncertain_transport_becomes_unknown(db, client, api_headers, merchant, credential):
    del credential
    with patch(
        "app.daraja.DarajaClient.stk_push",
        new=AsyncMock(side_effect=TimeoutError("read timeout")),
    ):
        response = _post_stk(client, api_headers, merchant, "UNCERTAIN")
    assert response.status_code == 201
    assert response.json()["status"] == "unknown"
    payment = db.query(Payment).filter_by(external_reference="UNCERTAIN").one()
    assert payment.checkout_request_id is None
    assert payment.attempts[0].status == "uncertain"


@pytest.mark.parametrize(
    ("provider_response", "expected_status"),
    [
        ({"ResponseCode": "1", "ResponseDescription": "Rejected"}, "failed"),
        ({"ResponseCode": "0", "ResponseDescription": "Accepted without identifier"}, "unknown"),
    ],
)
def test_stk_provider_rejection_and_missing_checkout_are_explicit(
    client, api_headers, merchant, credential, provider_response, expected_status
):
    del credential
    reference = f"PROVIDER-{expected_status}"
    with patch(
        "app.daraja.DarajaClient.stk_push",
        new=AsyncMock(return_value=(provider_response, {"Password": "proof"})),
    ):
        response = _post_stk(client, api_headers, merchant, reference)
    assert response.status_code == 201
    assert response.json()["status"] == expected_status


def test_callback_success_updates_payment_and_preserves_raw_payload(
    db, client, auth_headers, api_headers, merchant, stk_payment, monkeypatch
):
    monkeypatch.setattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", "")
    payload = _success_callback()
    response = client.post(f"{BASE}/callbacks/mpesa/{merchant['id']}", json=payload)
    assert response.status_code == 200
    payment = db.query(Payment).filter(Payment.id == stk_payment["id"]).one()
    callback = db.query(MpesaCallback).filter(MpesaCallback.payment_id == payment.id).one()
    assert payment.status == "success"
    assert payment.mpesa_receipt_number == "SLP100ABC"
    assert callback.raw_payload == payload
    assert callback.raw_body
    assert callback.processed is True

    normalized = client.get(f"{BASE}/callbacks/{callback.id}", headers=api_headers)
    assert normalized.status_code == 200
    assert "raw_payload" not in normalized.json()

    raw_key_response = client.post(
        f"{BASE}/api-keys",
        headers=auth_headers,
        json={
            "name": "Callback evidence reader",
            "merchant_id": merchant["id"],
            "environment": "sandbox",
            "scopes": ["callbacks:read", "callbacks:read_raw"],
        },
    )
    assert raw_key_response.status_code == 201, raw_key_response.text
    raw_key = raw_key_response.json()["api_key"]
    evidence = client.get(f"{BASE}/callbacks/{callback.id}", headers={"X-API-Key": raw_key})
    assert evidence.status_code == 200
    assert "raw_payload" not in evidence.json()


def test_callback_failure_updates_payment(db, client, merchant, stk_payment, monkeypatch):
    monkeypatch.setattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", "")
    response = client.post(f"{BASE}/callbacks/mpesa/{merchant['id']}", json=_failure_callback())
    assert response.status_code == 200
    payment = db.query(Payment).filter(Payment.id == stk_payment["id"]).one()
    assert payment.status == "failed"
    assert payment.result_code == "1032"


def test_unrecognized_callback_result_is_reviewable_not_terminal_failure(
    db, client, merchant, stk_payment, monkeypatch
):
    monkeypatch.setattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", "")
    payload = _failure_callback()
    payload["Body"]["stkCallback"]["ResultCode"] = 5555
    payload["Body"]["stkCallback"]["ResultDesc"] = "A new provider result not yet classified"
    response = client.post(f"{BASE}/callbacks/mpesa/{merchant['id']}", json=payload)
    assert response.status_code == 200
    payment = db.query(Payment).filter_by(id=stk_payment["id"]).one()
    assert payment.status == "unknown"
    assert payment.review_status == "needs_review"
    assert payment.review_reason == "provider_result_unrecognized_provider_result"
    assert payment.failed_at is None


def test_duplicate_callback_is_stored_but_idempotent(
    db, client, merchant, stk_payment, monkeypatch
):
    monkeypatch.setattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", "")
    payload = _success_callback()
    client.post(f"{BASE}/callbacks/mpesa/{merchant['id']}", json=payload)
    client.post(f"{BASE}/callbacks/mpesa/{merchant['id']}", json=payload)
    callbacks = db.query(MpesaCallback).filter(MpesaCallback.payment_id == stk_payment["id"]).all()
    assert len(callbacks) == 2
    assert sum(item.duplicate_of_callback_id is not None for item in callbacks) == 1
    assert (
        db.query(PaymentLedgerEntry)
        .filter(
            PaymentLedgerEntry.payment_id == stk_payment["id"],
            PaymentLedgerEntry.event_type == "payment.success",
        )
        .count()
        == 1
    )


def test_invalid_success_callback_does_not_block_later_valid_success(
    db, client, merchant, stk_payment, monkeypatch
):
    monkeypatch.setattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", "")
    invalid = _success_callback()
    invalid["Body"]["stkCallback"]["CallbackMetadata"]["Item"][0]["Value"] = 99
    assert client.post(f"{BASE}/callbacks/mpesa/{merchant['id']}", json=invalid).status_code == 200
    payment = db.query(Payment).filter_by(id=stk_payment["id"]).one()
    db.refresh(payment)
    assert payment.status == "unknown"
    assert db.query(MpesaCallback).one().processing_status == "verification_failed"

    assert (
        client.post(
            f"{BASE}/callbacks/mpesa/{merchant['id']}", json=_success_callback()
        ).status_code
        == 200
    )
    db.refresh(payment)
    assert payment.status == "success"
    statuses = {row.processing_status for row in db.query(MpesaCallback).all()}
    assert statuses == {"verification_failed", "processed_success"}


def test_success_callback_with_different_receipt_is_conflict(
    db, client, merchant, stk_payment, monkeypatch
):
    monkeypatch.setattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", "")
    client.post(f"{BASE}/callbacks/mpesa/{merchant['id']}", json=_success_callback())
    client.post(
        f"{BASE}/callbacks/mpesa/{merchant['id']}",
        json=_success_callback(receipt="DIFFERENT-RECEIPT"),
    )
    callbacks = db.query(MpesaCallback).order_by(MpesaCallback.received_at).all()
    assert callbacks[-1].processing_status == "conflict"
    assert db.query(AuditLog).filter_by(action="callback_conflict_detected").count() == 1
    assert (
        db.query(PaymentLedgerEntry)
        .filter_by(payment_id=stk_payment["id"], event_type="payment.success")
        .count()
        == 1
    )


def test_missing_receipt_then_valid_success_is_processed(
    db, client, merchant, stk_payment, monkeypatch
):
    monkeypatch.setattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", "")
    missing = _success_callback()
    items = missing["Body"]["stkCallback"]["CallbackMetadata"]["Item"]
    missing["Body"]["stkCallback"]["CallbackMetadata"]["Item"] = [
        item for item in items if item["Name"] != "MpesaReceiptNumber"
    ]
    client.post(f"{BASE}/callbacks/mpesa/{merchant['id']}", json=missing)
    client.post(f"{BASE}/callbacks/mpesa/{merchant['id']}", json=_success_callback())
    payment = db.query(Payment).filter_by(id=stk_payment["id"]).one()
    assert payment.status == "success"


def test_failure_then_success_is_recorded_as_conflict(
    db, client, merchant, stk_payment, monkeypatch
):
    monkeypatch.setattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", "")
    client.post(f"{BASE}/callbacks/mpesa/{merchant['id']}", json=_failure_callback())
    client.post(f"{BASE}/callbacks/mpesa/{merchant['id']}", json=_success_callback())
    payment = db.query(Payment).filter_by(id=stk_payment["id"]).one()
    assert payment.status == "failed"
    assert (
        db.query(MpesaCallback).order_by(MpesaCallback.received_at.desc()).first().processing_status
        == "conflict"
    )


def test_oversized_callback_is_truncated_stored_and_rejected(db, client, merchant, monkeypatch):
    monkeypatch.setattr(settings, "MAX_CALLBACK_BODY_BYTES", 128)
    response = client.post(
        f"{BASE}/callbacks/mpesa/{merchant['id']}",
        content=b"x" * 512,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    callback = db.query(MpesaCallback).one()
    assert callback.processing_status == "oversized"
    assert len(callback.raw_body.encode()) <= 128


def test_duplicate_receipt_does_not_complete_second_payment(
    db, client, merchant, stk_payment, monkeypatch
):
    monkeypatch.setattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", "")
    client.post(f"{BASE}/callbacks/mpesa/{merchant['id']}", json=_success_callback())
    first = db.query(Payment).filter(Payment.id == stk_payment["id"]).one()
    second = Payment(
        organization_id=first.organization_id,
        merchant_account_id=first.merchant_account_id,
        external_reference="ORDER-SECOND",
        customer_phone="254712345678",
        amount=Decimal("100.00"),
        currency="KES",
        description="Second order",
        status="stk_sent",
        checkout_request_id="ws_CO_SECOND",
    )
    db.add(second)
    db.commit()
    client.post(
        f"{BASE}/callbacks/mpesa/{merchant['id']}",
        json=_success_callback(checkout_id="ws_CO_SECOND", receipt="SLP100ABC"),
    )
    db.refresh(second)
    assert second.status == "stk_sent"
    assert second.mpesa_receipt_number is None
    assert db.query(Payment).filter(Payment.mpesa_receipt_number == "SLP100ABC").count() == 1


def test_payment_state_machine_blocks_terminal_regression():
    ensure_transition("stk_sent", "success")
    ensure_transition("unknown", "success")
    ensure_transition("success", "reversed")
    with pytest.raises(InvalidPaymentTransitionError):
        ensure_transition("success", "pending")
    with pytest.raises(InvalidPaymentTransitionError):
        ensure_transition("failed", "success")


def test_webhook_delivery_is_queued_and_replay_creates_new_attempt(
    db, client, api_headers, merchant, stk_payment, monkeypatch
):
    endpoint_response = client.post(
        f"{BASE}/webhooks/endpoints",
        headers=api_headers,
        json={
            "merchant_id": merchant["id"],
            "url": "https://merchant.example.test/lynxpay-events",
            "event_types": ["payment.success"],
        },
    )
    assert endpoint_response.status_code == 201, endpoint_response.text
    endpoint = (
        db.query(WebhookEndpoint).filter(WebhookEndpoint.id == endpoint_response.json()["id"]).one()
    )
    assert endpoint.secret_encrypted.startswith("env1::v1::")
    endpoint_listing = client.get(f"{BASE}/webhooks/endpoints", headers=api_headers)
    assert endpoint_listing.status_code == 200
    assert endpoint_listing.json()["items"][0]["id"] == endpoint.id
    monkeypatch.setattr(settings, "MPESA_CALLBACK_IP_ALLOWLIST", "")
    client.post(f"{BASE}/callbacks/mpesa/{merchant['id']}", json=_success_callback())
    delivery = (
        db.query(WebhookDelivery).filter(WebhookDelivery.payment_id == stk_payment["id"]).one()
    )
    assert delivery.status == "queued"
    replay = client.post(f"{BASE}/webhooks/deliveries/{delivery.id}/replay", headers=api_headers)
    assert replay.status_code == 201
    assert replay.json()["replay_of_delivery_id"] == delivery.id
    assert (
        db.query(WebhookDelivery).filter(WebhookDelivery.payment_id == stk_payment["id"]).count()
        == 2
    )
    deliveries = client.get(f"{BASE}/webhooks/deliveries", headers=api_headers)
    assert deliveries.status_code == 200
    assert {item["id"] for item in deliveries.json()["items"]} == {
        delivery.id,
        replay.json()["id"],
    }
    disabled = client.patch(
        f"{BASE}/webhooks/endpoints/{endpoint.id}",
        headers=api_headers,
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"


def test_api_key_cannot_view_another_merchant_payment(
    db, client, api_headers, merchant, stk_payment
):
    other_org = Organization(name="Other Org", contact_email="other@example.test", status="active")
    db.add(other_org)
    db.flush()
    other_merchant = MerchantAccount(
        organization_id=other_org.id,
        merchant_name="Other Merchant",
        shortcode="654321",
        shortcode_type="paybill",
        environment="sandbox",
        status="active",
        callback_url="https://other.example.test/callback",
    )
    db.add(other_merchant)
    db.flush()
    other_payment = Payment(
        organization_id=other_org.id,
        merchant_account_id=other_merchant.id,
        external_reference="OTHER-ORDER",
        customer_phone="254700000001",
        amount=Decimal("50.00"),
        currency="KES",
        description="Other tenant payment",
        status="created",
    )
    db.add(other_payment)
    db.commit()
    response = client.get(f"{BASE}/payments/{other_payment.id}", headers=api_headers)
    assert response.status_code == 404
    listing = client.get(f"{BASE}/payments", headers=api_headers)
    visible_ids = {item["id"] for item in listing.json()["items"]}
    assert stk_payment["id"] in visible_ids
    assert other_payment.id not in visible_ids
    assert {item["merchant_id"] for item in listing.json()["items"]} == {merchant["id"]}


def test_unauthorized_access_is_blocked(client):
    assert client.get(f"{BASE}/payments").status_code == 401
    assert client.get(f"{BASE}/callbacks").status_code == 401
