"""Version 2 payment retry and explicit evidence semantics."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.daraja import DarajaRequestNotSentError
from app.maintenance import abandon_stale_stk_submissions
from app.models import AuditLog, MpesaCallback, Payment, PaymentAttempt, PaymentLedgerEntry, User
from app.reconciliation import reconcile_payment
from app.service import transition_and_record, utcnow

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


def test_stk_attempt_is_committed_as_submitting_before_provider_io(
    db, client, auth_headers, merchant, credential
):
    del credential

    async def inspect_submission_boundary(*_args, **_kwargs):
        attempt = db.query(PaymentAttempt).filter_by(merchant_account_id=merchant["id"]).one()
        assert attempt.status == "submitting"
        assert attempt.submission_started_at is not None
        return _accepted("ws_V2_DURABLE_BOUNDARY")

    with patch("app.router.DarajaClient.stk_push", new=inspect_submission_boundary):
        response = client.post(
            f"{BASE}/payments/stk-push",
            headers={**auth_headers, "Idempotency-Key": "v2-durable-boundary"},
            json=_request(merchant["id"], "V2-DURABLE-BOUNDARY"),
        )
    assert response.status_code == 201, response.text
    assert response.json()["attempt"]["status"] == "accepted"
    assert response.json()["attempt"]["provider_responded_at"] is not None


def test_stale_submitting_attempt_is_abandoned_unknown_and_audited(db, merchant):
    payment = Payment(
        organization_id=merchant["organization_id"],
        merchant_account_id=merchant["id"],
        external_reference="V2-CRASH-RECOVERY",
        customer_phone="254712345678",
        amount="250.00",
        currency="KES",
        description="Crash recovery",
        status="pending",
        provider_acceptance_state="not_sent",
    )
    db.add(payment)
    db.flush()
    attempt = PaymentAttempt(
        payment_id=payment.id,
        merchant_account_id=merchant["id"],
        attempt_number=1,
        phone_number=payment.customer_phone,
        amount=payment.amount,
        request_payload_redacted={},
        status="submitting",
        submission_started_at=utcnow() - timedelta(minutes=10),
    )
    db.add(attempt)
    db.commit()

    recovered = abandon_stale_stk_submissions(db)
    assert recovered == [attempt.id]
    db.refresh(payment)
    db.refresh(attempt)
    assert attempt.status == "abandoned"
    assert attempt.abandoned_at is not None
    assert payment.status == "unknown"
    assert payment.provider_acceptance_state == "uncertain"
    assert payment.review_status == "needs_review"
    assert payment.review_reason == "stale_stk_submission_abandoned"
    assert (
        db.query(PaymentLedgerEntry)
        .filter_by(payment_id=payment.id, event_type="payment.unknown")
        .count()
        == 1
    )
    assert db.query(AuditLog).filter_by(action="stk_submission_abandoned").count() == 1
    assert abandon_stale_stk_submissions(db) == []


def test_direct_payment_status_mutation_without_ledger_is_rejected(db, stk_payment):
    payment = db.query(Payment).filter_by(id=stk_payment["id"]).one()
    payment.status = "success"
    with pytest.raises(ValueError, match="ledger entry"):
        db.commit()
    db.rollback()
    assert db.query(Payment).filter_by(id=payment.id).one().status == "stk_sent"


def test_platform_admin_can_link_verified_unmatched_success_callback(
    db, client, auth_headers, merchant, stk_payment
):
    unmatched = client.post(
        f"{BASE}/callbacks/mpesa/{merchant['id']}",
        json=_success_callback("ws_PROVIDER_ID_NOT_CAPTURED", "V2LINKEDRECEIPT"),
    )
    assert unmatched.status_code == 200
    callback = db.query(MpesaCallback).filter_by(mpesa_receipt_number="V2LINKEDRECEIPT").one()
    assert callback.processing_status == "unmatched"

    denied = client.post(
        f"{BASE}/admin/callbacks/{callback.id}/link-payment",
        headers=auth_headers,
        json={
            "payment_id": stk_payment["id"],
            "reason": "Support reviewed the raw provider callback evidence",
        },
    )
    assert denied.status_code == 403

    registered = client.post(
        f"{BASE}/auth/register",
        json={
            "organization_name": "LynxPay Platform Operations",
            "contact_email": "callback-reviewer@lynxpay.co.ke",
            "full_name": "Callback Reviewer",
            "password": "platform-reviewer-secure-password",
        },
    )
    assert registered.status_code == 201, registered.text
    reviewer = db.query(User).filter_by(email="callback-reviewer@lynxpay.co.ke").one()
    reviewer.is_platform_admin = True
    db.commit()
    platform_headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    linked = client.post(
        f"{BASE}/admin/callbacks/{callback.id}/link-payment",
        headers=platform_headers,
        json={
            "payment_id": stk_payment["id"],
            "reason": "Support verified merchant, amount, phone, and receipt evidence",
        },
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["payment_status"] == "success"
    db.refresh(callback)
    payment = db.query(Payment).filter_by(id=stk_payment["id"]).one()
    assert callback.payment_id == payment.id
    assert callback.linked_by_user_id == reviewer.id
    assert payment.mpesa_receipt_number == "V2LINKEDRECEIPT"
    assert payment.success_source == "callback"
    assert (
        db.query(AuditLog)
        .filter_by(entity_id=callback.id, action="unmatched_callback_linked")
        .count()
        == 1
    )
    replay = client.post(
        f"{BASE}/admin/callbacks/{callback.id}/link-payment",
        headers=platform_headers,
        json={
            "payment_id": payment.id,
            "reason": "A replay must not process the same callback twice",
        },
    )
    assert replay.status_code == 409


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
    transition_and_record(
        db,
        payment=payment,
        target="unknown",
        event_type="payment.unknown",
        details={"source": "test_manual_review_age"},
    )
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


@pytest.mark.asyncio
async def test_callback_success_wins_while_reconciliation_network_call_is_in_flight(
    db, stk_payment
):
    async def provider_returns_stale_failure(*_args, **_kwargs):
        payment = db.query(Payment).filter_by(id=stk_payment["id"]).one()
        transition_and_record(
            db,
            payment=payment,
            target="success",
            event_type="payment.success",
            details={"source": "callback_during_reconciliation"},
        )
        payment.mpesa_receipt_number = "CALLBACK-WINS-1"
        payment.result_code = "0"
        payment.result_description = "Callback confirmed success"
        payment.success_source = "callback"
        payment.receipt_status = "present"
        payment.paid_at = utcnow()
        db.commit()
        return {"ResultCode": "1032", "ResultDesc": "Stale cancellation response"}, {}

    with patch(
        "app.reconciliation.DarajaClient.query_stk_status", new=provider_returns_stale_failure
    ):
        check = await reconcile_payment(db, stk_payment["id"])

    payment = db.query(Payment).filter_by(id=stk_payment["id"]).one()
    assert check.outcome == "superseded"
    assert payment.status == "success"
    assert payment.mpesa_receipt_number == "CALLBACK-WINS-1"
    assert payment.result_code == "0"
    assert payment.success_source == "callback"
