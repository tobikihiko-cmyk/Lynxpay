"""Maker-checker M-PESA reversal workflow tests."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
import uuid

from app.core.security import create_access_token, hash_password
from app.models import (
    AuditLog,
    AuthSession,
    DarajaCredential,
    Payment,
    PaymentLedgerEntry,
    ReversalCallback,
    ReversalRequest,
    User,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.reversal_controls import bind_reversal_to_payment
from app.reversals import claim_reversals, submit_claimed_reversal
from app.service import utcnow

BASE = "/api/v1"


def _second_admin_headers(db) -> dict[str, str]:
    owner = db.query(User).filter(User.role == "owner").one()
    admin = User(
        organization_id=owner.organization_id,
        email="approver@acme.co.ke",
        full_name="Reversal Approver",
        password_hash=hash_password("correct-horse-battery-staple"),
        role="admin",
        status="active",
        email_verified_at=utcnow(),
    )
    db.add(admin)
    db.flush()
    session = AuthSession(
        organization_id=owner.organization_id,
        user_id=admin.id,
        family_id=str(uuid.uuid4()),
        refresh_token_prefix=f"test_{uuid.uuid4().hex[:12]}",
        refresh_token_hash=uuid.uuid4().hex,
        status="active",
        expires_at=utcnow() + timedelta(days=1),
        mfa_authenticated_at=utcnow(),
    )
    db.add(session)
    db.commit()
    return {"Authorization": f"Bearer {create_access_token(admin.id, session.id)}"}


def _merchant_with_reversal_credentials(client, auth_headers) -> dict:
    merchant_response = client.post(
        f"{BASE}/merchants",
        headers=auth_headers,
        json={
            "merchant_name": "Acme Reversals",
            "shortcode": "123456",
            "shortcode_type": "paybill",
            "environment": "sandbox",
        },
    )
    assert merchant_response.status_code == 201, merchant_response.text
    merchant = merchant_response.json()
    credential_response = client.post(
        f"{BASE}/merchants/{merchant['id']}/daraja-credentials",
        headers=auth_headers,
        json={
            "consumer_key": "consumer-key-secret",
            "consumer_secret": "consumer-secret-value",
            "passkey": "daraja-passkey-value",
            "initiator_name": "testapi",
            "security_credential": "encrypted-by-safaricom-certificate",
            "shortcode": "123456",
            "environment": "sandbox",
        },
    )
    assert credential_response.status_code == 201, credential_response.text
    return merchant


def test_reversal_requires_distinct_approval_and_provider_callback(db, client, auth_headers):
    merchant = _merchant_with_reversal_credentials(client, auth_headers)
    owner = db.query(User).filter(User.role == "owner").one()
    payment = Payment(
        organization_id=owner.organization_id,
        merchant_account_id=merchant["id"],
        external_reference="REVERSAL-E2E-001",
        customer_phone="254712345678",
        amount=Decimal("750.00"),
        currency="KES",
        description="Legal consultation",
        purpose="payment",
        status="success",
        success_source="callback",
        receipt_status="present",
        provider_acceptance_state="accepted",
        checkout_request_id="ws_CO_reversal_test",
        mpesa_receipt_number="QHX123TEST",
        result_code="0",
        paid_at=utcnow(),
    )
    db.add(payment)
    endpoint = WebhookEndpoint(
        organization_id=owner.organization_id,
        merchant_account_id=merchant["id"],
        url="https://merchant.example.test/payment-events",
        event_types=["payment.reversed"],
        secret_encrypted="test-encrypted-secret",
        encryption_key_version="test",
        status="active",
    )
    db.add(endpoint)
    db.commit()

    response = client.post(
        f"{BASE}/payments/{payment.id}/reversals",
        headers={**auth_headers, "Idempotency-Key": "reverse-QHX123TEST"},
        json={"reason": "Customer was charged for a cancelled service"},
    )
    assert response.status_code == 201, response.text
    reversal_id = response.json()["id"]
    assert response.json()["status"] == "pending_approval"

    replay = client.post(
        f"{BASE}/payments/{payment.id}/reversals",
        headers={**auth_headers, "Idempotency-Key": "reverse-QHX123TEST"},
        json={"reason": "Customer was charged for a cancelled service"},
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == reversal_id
    assert replay.json()["idempotent_replay"] is True
    conflict = client.post(
        f"{BASE}/payments/{payment.id}/reversals",
        headers={**auth_headers, "Idempotency-Key": "reverse-QHX123TEST"},
        json={"reason": "A different customer reason must not reuse the key"},
    )
    assert conflict.status_code == 409

    self_approval = client.post(
        f"{BASE}/reversals/{reversal_id}/approve",
        headers=auth_headers,
        json={"note": "Approve refund"},
    )
    assert self_approval.status_code == 409

    approver_headers = _second_admin_headers(db)
    approval = client.post(
        f"{BASE}/reversals/{reversal_id}/approve",
        headers=approver_headers,
        json={"note": "Receipt and customer request verified"},
    )
    assert approval.status_code == 200, approval.text
    assert approval.json()["status"] == "approved"
    reversal = db.query(ReversalRequest).filter_by(id=reversal_id).one()
    assert reversal.response_payload["approval"]["note"] == (
        "Receipt and customer request verified"
    )

    claimed = claim_reversals(db, "reversal-worker", 10)
    assert claimed == [reversal_id]
    provider_response = {
        "OriginatorConversationID": "orig-reversal-001",
        "ConversationID": "conv-reversal-001",
        "ResponseCode": "0",
        "ResponseDescription": "Accept the service request successfully.",
    }
    with patch(
        "app.reversals.DarajaClient.reverse_transaction",
        new=AsyncMock(return_value=(provider_response, {"SecurityCredential": "secret"})),
    ):
        submitted = asyncio.run(submit_claimed_reversal(db, reversal_id, "reversal-worker"))
    assert submitted is not None
    assert submitted.status == "submitted"
    assert (
        submitted.request_payload_redacted["provider_request"]["SecurityCredential"] == "********"
    )
    assert submitted.request_payload_redacted["binding"]["mpesa_receipt_number"] == "QHX123TEST"

    callback = client.post(
        f"{BASE}/callbacks/mpesa/reversals/{merchant['id']}/result",
        json={
            "Result": {
                "ResultType": 0,
                "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "OriginatorConversationID": "orig-reversal-001",
                "ConversationID": "conv-reversal-001",
                "TransactionID": "REV-QHX123TEST",
            }
        },
    )
    assert callback.status_code == 200, callback.text
    db.refresh(payment)
    assert payment.status == "reversed"
    reversal = db.query(ReversalRequest).filter_by(id=reversal_id).one()
    assert reversal.status == "succeeded"
    assert reversal.provider_transaction_id == "REV-QHX123TEST"
    assert (
        db.query(PaymentLedgerEntry)
        .filter_by(
            payment_id=payment.id,
            event_type="payment.reversed",
            status_from="success",
            status_to="reversed",
        )
        .count()
        == 1
    )
    assert (
        db.query(ReversalCallback)
        .filter_by(reversal_request_id=reversal_id, processing_status="processed_success")
        .count()
        == 1
    )
    assert (
        db.query(WebhookDelivery)
        .filter_by(payment_id=payment.id, event_type="payment.reversed")
        .count()
        == 1
    )

    duplicate = client.post(
        f"{BASE}/callbacks/mpesa/reversals/{merchant['id']}/result",
        json={
            "Result": {
                "ResultCode": 0,
                "OriginatorConversationID": "orig-reversal-001",
                "ConversationID": "conv-reversal-001",
                "TransactionID": "REV-QHX123TEST",
            }
        },
    )
    assert duplicate.status_code == 200
    assert (
        db.query(PaymentLedgerEntry)
        .filter_by(payment_id=payment.id, event_type="payment.reversed")
        .count()
        == 1
    )
    assert (
        db.query(ReversalCallback)
        .filter_by(reversal_request_id=reversal_id, processing_status="duplicate")
        .count()
        == 1
    )


def test_reversal_approval_requires_operator_credentials(db, client, auth_headers):
    merchant_response = client.post(
        f"{BASE}/merchants",
        headers=auth_headers,
        json={
            "merchant_name": "No Reversal Credentials",
            "shortcode": "654321",
            "shortcode_type": "paybill",
            "environment": "sandbox",
        },
    )
    merchant = merchant_response.json()
    credential = DarajaCredential(
        merchant_account_id=merchant["id"],
        consumer_key_encrypted="not-encrypted",
        consumer_secret_encrypted="not-encrypted",
        passkey_encrypted="not-encrypted",
        shortcode="654321",
        environment="sandbox",
        is_active=True,
    )
    owner = db.query(User).filter(User.role == "owner").one()
    payment = Payment(
        organization_id=owner.organization_id,
        merchant_account_id=merchant["id"],
        external_reference="REVERSAL-NO-CREDENTIALS",
        customer_phone="254712345678",
        amount=Decimal("100.00"),
        description="Reversal credential test",
        status="success",
        purpose="payment",
        receipt_status="present",
        mpesa_receipt_number="NO-CREDS-RECEIPT",
    )
    db.add_all([credential, payment])
    db.commit()
    requested = client.post(
        f"{BASE}/payments/{payment.id}/reversals",
        headers={**auth_headers, "Idempotency-Key": "no-credentials-reversal"},
        json={"reason": "Customer requested cancellation and full reversal"},
    )
    approver_headers = _second_admin_headers(db)
    approval = client.post(
        f"{BASE}/reversals/{requested.json()['id']}/approve",
        headers=approver_headers,
        json={"note": "Receipt evidence independently reviewed"},
    )
    assert approval.status_code == 409
    assert "initiator name and security credential" in approval.json()["detail"]


def test_reversal_binding_amount_and_timeout_controls(db, client, auth_headers):
    merchant = _merchant_with_reversal_credentials(client, auth_headers)
    owner = db.query(User).filter(User.role == "owner").one()
    fractional = Payment(
        organization_id=owner.organization_id,
        merchant_account_id=merchant["id"],
        external_reference="REVERSAL-FRACTIONAL",
        customer_phone="254712345678",
        amount=Decimal("100.50"),
        currency="KES",
        description="Fractional reversal guard",
        purpose="payment",
        status="success",
        receipt_status="present",
        mpesa_receipt_number="FRACTIONAL-RECEIPT",
    )
    payment = Payment(
        organization_id=owner.organization_id,
        merchant_account_id=merchant["id"],
        external_reference="REVERSAL-TIMEOUT",
        customer_phone="254712345678",
        amount=Decimal("300.00"),
        currency="KES",
        description="Timeout reversal guard",
        purpose="payment",
        status="success",
        receipt_status="present",
        mpesa_receipt_number="TIMEOUT-RECEIPT",
    )
    db.add_all([fractional, payment])
    db.commit()

    rejected_amount = client.post(
        f"{BASE}/payments/{fractional.id}/reversals",
        headers={**auth_headers, "Idempotency-Key": "fractional-reversal"},
        json={"reason": "Customer requested a reversal of fractional amount"},
    )
    assert rejected_amount.status_code == 409
    assert "whole KES" in rejected_amount.json()["detail"]

    requested = client.post(
        f"{BASE}/payments/{payment.id}/reversals",
        headers={**auth_headers, "Idempotency-Key": "timeout-reversal-one"},
        json={"reason": "Customer cancellation requires a full reversal"},
    )
    assert requested.status_code == 201
    reversal = db.query(ReversalRequest).filter_by(id=requested.json()["id"]).one()
    reversal.status = "timeout"
    db.commit()

    duplicate = client.post(
        f"{BASE}/payments/{payment.id}/reversals",
        headers={**auth_headers, "Idempotency-Key": "timeout-reversal-two"},
        json={"reason": "A timeout must remain an active reversal request"},
    )
    assert duplicate.status_code == 409
    assert reversal.id in duplicate.json()["detail"]


def test_reversal_approval_revalidates_binding_and_requires_reason(db, client, auth_headers):
    merchant = _merchant_with_reversal_credentials(client, auth_headers)
    owner = db.query(User).filter(User.role == "owner").one()
    payment = Payment(
        organization_id=owner.organization_id,
        merchant_account_id=merchant["id"],
        external_reference="REVERSAL-BINDING",
        customer_phone="254712345678",
        amount=Decimal("400.00"),
        currency="KES",
        description="Binding validation",
        purpose="payment",
        status="success",
        receipt_status="present",
        mpesa_receipt_number="BOUND-RECEIPT",
    )
    db.add(payment)
    db.commit()
    requested = client.post(
        f"{BASE}/payments/{payment.id}/reversals",
        headers={**auth_headers, "Idempotency-Key": "binding-reversal"},
        json={"reason": "Customer supplied evidence for a full reversal"},
    )
    reversal_id = requested.json()["id"]
    approver_headers = _second_admin_headers(db)

    missing_reason = client.post(
        f"{BASE}/reversals/{reversal_id}/approve",
        headers=approver_headers,
        json={},
    )
    assert missing_reason.status_code == 422

    payment.mpesa_receipt_number = "TAMPERED-RECEIPT"
    db.commit()
    conflict = client.post(
        f"{BASE}/reversals/{reversal_id}/approve",
        headers=approver_headers,
        json={"note": "Customer evidence and receipt reviewed"},
    )
    assert conflict.status_code == 409
    assert "binding changed" in conflict.json()["detail"]
    reversal = db.query(ReversalRequest).filter_by(id=reversal_id).one()
    assert reversal.status == "pending_approval"
    assert (
        db.query(AuditLog)
        .filter_by(entity_id=reversal_id, action="reversal_approval_conflict")
        .count()
        == 1
    )


def test_terminal_reversal_callback_cannot_rewrite_state(db, client, auth_headers):
    merchant = _merchant_with_reversal_credentials(client, auth_headers)
    owner = db.query(User).filter(User.role == "owner").one()
    payment = Payment(
        organization_id=owner.organization_id,
        merchant_account_id=merchant["id"],
        external_reference="REVERSAL-TERMINAL",
        customer_phone="254712345678",
        amount=Decimal("500.00"),
        currency="KES",
        description="Terminal state protection",
        purpose="payment",
        status="success",
        receipt_status="present",
        mpesa_receipt_number="TERMINAL-RECEIPT",
    )
    reversal = ReversalRequest(
        organization_id=owner.organization_id,
        merchant_account_id=merchant["id"],
        payment=payment,
        idempotency_key="terminal-digest",
        idempotency_request_hash="terminal-fingerprint",
        amount=payment.amount,
        currency="KES",
        reason="Provider rejected the original reversal request",
        status="failed",
        requested_by_user_id=owner.id,
        originator_conversation_id="terminal-originator",
        conversation_id="terminal-conversation",
    )
    bind_reversal_to_payment(reversal, payment)
    db.add_all([payment, reversal])
    db.commit()

    callback = client.post(
        f"{BASE}/callbacks/mpesa/reversals/{merchant['id']}/result",
        json={
            "Result": {
                "ResultCode": 0,
                "OriginatorConversationID": "terminal-originator",
                "ConversationID": "terminal-conversation",
                "TransactionID": "LATE-REVERSAL",
            }
        },
    )
    assert callback.status_code == 200
    db.refresh(reversal)
    db.refresh(payment)
    assert reversal.status == "failed"
    assert payment.status == "success"
    assert (
        db.query(ReversalCallback)
        .filter_by(
            reversal_request_id=reversal.id,
            processing_status="terminal_state_conflict",
        )
        .count()
        == 1
    )


def test_reversal_status_query_recovers_ambiguous_timeout(db, client, auth_headers):
    merchant = _merchant_with_reversal_credentials(client, auth_headers)
    owner = db.query(User).filter(User.role == "owner").one()
    payment = Payment(
        organization_id=owner.organization_id,
        merchant_account_id=merchant["id"],
        external_reference="REVERSAL-STATUS-QUERY",
        customer_phone="254712345678",
        amount=Decimal("600.00"),
        currency="KES",
        description="Status query recovery",
        purpose="payment",
        status="success",
        receipt_status="present",
        mpesa_receipt_number="QUERY-RECEIPT",
    )
    db.add(payment)
    db.flush()
    reversal = ReversalRequest(
        organization_id=owner.organization_id,
        merchant_account_id=merchant["id"],
        payment=payment,
        idempotency_key="query-digest",
        idempotency_request_hash="query-fingerprint",
        amount=payment.amount,
        currency="KES",
        reason="Provider timeout requires an authoritative status query",
        status="timeout",
        requested_by_user_id=owner.id,
    )
    bind_reversal_to_payment(reversal, payment)
    db.add(reversal)
    db.commit()

    provider_response = {
        "ResponseCode": "0",
        "OriginatorConversationID": "status-query-originator",
        "ConversationID": "status-query-conversation",
    }
    provider_request = {
        "Initiator": "testapi",
        "SecurityCredential": "provider-secret",
        "TransactionID": "QUERY-RECEIPT",
    }
    with patch(
        "app.routers.reversals.DarajaClient.query_transaction_status",
        new=AsyncMock(return_value=(provider_response, provider_request)),
    ):
        query_response = client.post(
            f"{BASE}/reversals/{reversal.id}/status-query",
            headers=auth_headers,
        )
    assert query_response.status_code == 202, query_response.text
    db.refresh(reversal)
    query_evidence = reversal.response_payload["status_queries"][0]
    assert query_evidence["state"] == "accepted"
    assert query_evidence["request"]["SecurityCredential"] == "********"

    callback = client.post(
        f"{BASE}/callbacks/mpesa/reversals/{merchant['id']}/status-result",
        json={
            "Result": {
                "ResultCode": 0,
                "ResultDesc": "Transaction status returned",
                "ResultParameters": {
                    "ResultParameter": [
                        {"Key": "TransactionID", "Value": "QUERY-RECEIPT"},
                        {"Key": "TransactionStatus", "Value": "Reversed"},
                    ]
                },
            }
        },
    )
    assert callback.status_code == 200, callback.text
    db.refresh(payment)
    db.refresh(reversal)
    assert payment.status == "reversed"
    assert reversal.status == "succeeded"
