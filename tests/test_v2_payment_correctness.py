"""Version 2 payment retry and explicit evidence semantics."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.daraja import DarajaRequestNotSentError
from app.models import AuditLog, Payment, PaymentAttempt, PaymentLedgerEntry
from app.service import utcnow

BASE = "/api/v1"


@pytest.fixture
def merchant(client, auth_headers):
    response = client.post(
        f"{BASE}/merchants",
        headers=auth_headers,
        json={
            "merchant_name": "Version 2 PayBill",
            "shortcode": "654321",
            "shortcode_type": "paybill",
            "environment": "sandbox",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def credential(db, client, auth_headers, merchant):
    created = client.post(
        f"{BASE}/merchants/{merchant['id']}/daraja-credentials",
        headers=auth_headers,
        json={
            "consumer_key": "v2-consumer-key",
            "consumer_secret": "v2-consumer-secret",
            "passkey": "v2-passkey",
            "shortcode": merchant["shortcode"],
            "environment": merchant["environment"],
        },
    )
    assert created.status_code == 201, created.text
    with patch(
        "app.router.DarajaClient.get_access_token", new=AsyncMock(return_value="v2-oauth-token")
    ):
        tested = client.post(
            f"{BASE}/merchants/{merchant['id']}/daraja-credentials/test",
            headers=auth_headers,
        )
    assert tested.status_code == 200, tested.text
    verification = Payment(
        organization_id=merchant["organization_id"],
        merchant_account_id=merchant["id"],
        external_reference=f"V2-VERIFY-{merchant['id']}",
        customer_phone="254712345678",
        amount="1.00",
        currency="KES",
        description="Version 2 verification",
        purpose="merchant_verification",
        status="success",
        checkout_request_id=f"v2-verify-{merchant['id']}",
        mpesa_receipt_number=f"V2VERIFY{merchant['id']}",
        result_code="0",
        success_source="callback",
        receipt_status="present",
        provider_acceptance_state="accepted",
    )
    db.add(verification)
    db.commit()
    activated = client.patch(
        f"{BASE}/merchants/{merchant['id']}", headers=auth_headers, json={"status": "active"}
    )
    assert activated.status_code == 200, activated.text
    return created.json()


@pytest.fixture
def stk_payment(client, auth_headers, merchant, credential):
    with patch(
        "app.router.DarajaClient.stk_push",
        new=AsyncMock(return_value=_accepted("ws_V2_BASE_PAYMENT")),
    ):
        response = client.post(
            f"{BASE}/payments/stk-push",
            headers={**auth_headers, "Idempotency-Key": "v2-base-payment"},
            json=_request(merchant["id"], "V2-BASE-PAYMENT"),
        )
    assert response.status_code == 201, response.text
    return response.json()


def _request(merchant_id: str, reference: str) -> dict:
    return {
        "merchant_id": merchant_id,
        "amount": "250.00",
        "phone_number": "0712345678",
        "external_reference": reference,
        "description": f"Payment {reference}",
    }


def _accepted(checkout_id: str) -> tuple[dict, dict]:
    return (
        {
            "ResponseCode": "0",
            "MerchantRequestID": f"MR-{checkout_id}",
            "CheckoutRequestID": checkout_id,
            "ResponseDescription": "Accepted for processing",
        },
        {
            "Password": "generated-secret",
            "PhoneNumber": "254712345678",
            "PartyA": "254712345678",
            "Amount": 250,
            "AccountReference": "redacted-by-service",
        },
    )


def _success_callback(checkout_id: str, receipt: str = "V2RECEIPT1") -> dict:
    return {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": f"MR-{checkout_id}",
                "CheckoutRequestID": checkout_id,
                "ResultCode": 0,
                "ResultDesc": "Processed successfully",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": 250},
                        {"Name": "MpesaReceiptNumber", "Value": receipt},
                        {"Name": "PhoneNumber", "Value": 254712345678},
                    ]
                },
            }
        }
    }


def test_retry_failed_payment_preserves_identity_creates_attempt_two_and_audits(
    db, client, auth_headers, merchant, credential
):
    with patch(
        "app.router.DarajaClient.stk_push",
        new=AsyncMock(side_effect=DarajaRequestNotSentError("connection not opened")),
    ):
        created = client.post(
            f"{BASE}/payments/stk-push",
            headers={**auth_headers, "Idempotency-Key": "v2-retry-failed"},
            json=_request(merchant["id"], "V2-RETRY-FAILED"),
        )
    assert created.status_code == 201, created.text
    original = created.json()
    assert original["status"] == "failed"
    assert original["provider_acceptance_state"] == "rejected"

    with patch(
        "app.router.DarajaClient.stk_push",
        new=AsyncMock(return_value=_accepted("ws_V2_RETRY_2")),
    ):
        retried = client.post(
            f"{BASE}/payments/{original['id']}/retry",
            headers=auth_headers,
            json={"reason": "Operator confirmed the request was never submitted"},
        )
    assert retried.status_code == 200, retried.text
    result = retried.json()
    assert result["id"] == original["id"]
    assert result["external_reference"] == "V2-RETRY-FAILED"
    assert result["status"] == "stk_sent"
    assert result["attempt"]["attempt_number"] == 2
    assert result["attempt"]["attempt_type"] == "retry"
    assert result["provider_acceptance_state"] == "accepted"

    attempts = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.payment_id == original["id"])
        .order_by(PaymentAttempt.attempt_number)
        .all()
    )
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]
    assert attempts[1].retry_reason == "Operator confirmed the request was never submitted"
    assert (
        db.query(AuditLog)
        .filter_by(entity_id=original["id"], action="payment_retry_initiated")
        .count()
        == 1
    )
    assert (
        db.query(PaymentLedgerEntry)
        .filter_by(payment_id=original["id"], event_type="payment.retry_started")
        .count()
        == 1
    )


def test_unknown_retry_requires_explicit_admin_override(client, auth_headers, merchant, credential):
    key = client.post(
        f"{BASE}/api-keys",
        headers=auth_headers,
        json={
            "name": "Version 2 retry key",
            "merchant_id": merchant["id"],
            "environment": "sandbox",
            "scopes": ["payments:read", "payments:write"],
        },
    )
    assert key.status_code == 201, key.text
    api_headers = {"X-API-Key": key.json()["api_key"]}
    with patch(
        "app.router.DarajaClient.stk_push",
        new=AsyncMock(side_effect=RuntimeError("transport outcome unavailable")),
    ):
        created = client.post(
            f"{BASE}/payments/stk-push",
            headers={**api_headers, "Idempotency-Key": "v2-retry-unknown"},
            json=_request(merchant["id"], "V2-RETRY-UNKNOWN"),
        )
    payment = created.json()
    assert payment["status"] == "unknown"
    assert payment["review_status"] == "needs_review"

    denied = client.post(
        f"{BASE}/payments/{payment['id']}/retry",
        headers=auth_headers,
        json={"reason": "Operator reviewed ambiguous transport evidence"},
    )
    assert denied.status_code == 409

    api_key_denied = client.post(
        f"{BASE}/payments/{payment['id']}/retry",
        headers=api_headers,
        json={
            "reason": "Integration attempted an uncertain payment retry",
            "allow_uncertain": True,
        },
    )
    assert api_key_denied.status_code == 403

    with patch(
        "app.router.DarajaClient.stk_push",
        new=AsyncMock(return_value=_accepted("ws_V2_UNKNOWN_RETRY_2")),
    ):
        allowed = client.post(
            f"{BASE}/payments/{payment['id']}/retry",
            headers=auth_headers,
            json={
                "reason": "Operator reviewed ambiguous transport evidence",
                "allow_uncertain": True,
            },
        )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["attempt"]["attempt_number"] == 2


def test_successful_payment_cannot_be_retried_and_callback_sets_receipt_evidence(
    client, auth_headers, merchant, stk_payment
):
    callback = client.post(
        f"{BASE}/callbacks/mpesa/{merchant['id']}",
        json=_success_callback(stk_payment["checkout_request_id"]),
    )
    assert callback.status_code == 200
    detail = client.get(f"{BASE}/payments/{stk_payment['id']}", headers=auth_headers)
    assert detail.status_code == 200
    evidence = detail.json()
    assert evidence["status"] == "success"
    assert evidence["success_source"] == "callback"
    assert evidence["receipt_status"] == "present"
    assert evidence["review_status"] == "none"

    retried = client.post(
        f"{BASE}/payments/{stk_payment['id']}/retry",
        headers=auth_headers,
        json={"reason": "This must never initiate a second successful charge"},
    )
    assert retried.status_code == 409


def test_same_idempotency_key_with_different_request_returns_conflict(
    client, auth_headers, merchant, credential
):
    with patch(
        "app.router.DarajaClient.stk_push",
        new=AsyncMock(return_value=_accepted("ws_V2_IDEMPOTENCY_1")),
    ):
        first = client.post(
            f"{BASE}/payments/stk-push",
            headers={**auth_headers, "Idempotency-Key": "v2-same-key"},
            json=_request(merchant["id"], "V2-IDEMPOTENCY-1"),
        )
    assert first.status_code == 201

    changed = _request(merchant["id"], "V2-IDEMPOTENCY-2")
    changed["amount"] = "251.00"
    second = client.post(
        f"{BASE}/payments/stk-push",
        headers={**auth_headers, "Idempotency-Key": "v2-same-key"},
        json=changed,
    )
    assert second.status_code == 409


def test_status_query_success_without_receipt_is_explicitly_reviewable(
    db, client, auth_headers, stk_payment
):
    with patch(
        "app.reconciliation.DarajaClient.query_stk_status",
        new=AsyncMock(
            return_value=(
                {"ResultCode": "0", "ResultDesc": "Processed successfully"},
                {"CheckoutRequestID": stk_payment["checkout_request_id"]},
            )
        ),
    ):
        reconciled = client.post(
            f"{BASE}/payments/{stk_payment['id']}/reconcile", headers=auth_headers
        )
    assert reconciled.status_code == 200, reconciled.text
    payment = db.query(Payment).filter(Payment.id == stk_payment["id"]).one()
    assert payment.status == "success"
    assert payment.success_source == "status_query"
    assert payment.receipt_status == "missing"
    assert payment.review_status == "needs_review"
    assert payment.review_reason == "status_query_success_missing_receipt"


def test_reconciliation_exhaustion_after_24_hours_moves_to_manual_review(
    db, client, auth_headers, stk_payment
):
    payment = db.query(Payment).filter(Payment.id == stk_payment["id"]).one()
    payment.created_at = utcnow() - timedelta(hours=25)
    payment.status = "unknown"
    db.commit()
    with patch(
        "app.reconciliation.DarajaClient.query_stk_status",
        new=AsyncMock(side_effect=RuntimeError("status endpoint unavailable")),
    ):
        reconciled = client.post(f"{BASE}/payments/{payment.id}/reconcile", headers=auth_headers)
    assert reconciled.status_code == 200, reconciled.text
    db.refresh(payment)
    assert payment.status == "unknown"
    assert payment.review_status == "needs_review"
    assert payment.review_reason == "reconciliation_exhausted"
    assert payment.next_reconciliation_at is None
