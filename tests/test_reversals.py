"""Maker-checker M-PESA reversal workflow tests."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
import uuid

from app.core.security import create_access_token, hash_password
from app.models import (
    AuthSession,
    DarajaCredential,
    Payment,
    PaymentLedgerEntry,
    ReversalCallback,
    ReversalRequest,
    User,
)
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
    assert submitted.request_payload_redacted["SecurityCredential"] == "********"

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
        json={},
    )
    assert approval.status_code == 409
    assert "initiator name and security credential" in approval.json()["detail"]
