"""Leased M-PESA reversal submission worker."""

from __future__ import annotations

from datetime import timedelta
import time

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.daraja import (
    DarajaClient,
    DarajaRequestNotSentError,
    DarajaSubmissionUncertainError,
    redact_reversal_payload,
)
from app.models import DarajaCredential, MerchantAccount, Payment, ReversalRequest
from app.observability import (
    DARAJA_REQUEST_DURATION,
    MPESA_RESULT_CODES,
    REVERSAL_SUBMISSIONS,
)
from app.reversal_controls import (
    merge_reversal_request_evidence,
    merge_reversal_response_evidence,
    reversal_binding_error,
)
from app.service import (
    audit,
    decrypted_reversal_credentials,
    decrypted_secrets,
    utcnow,
)


def claim_reversals(db: Session, worker_id: str, limit: int = 20) -> list[str]:
    now = utcnow()
    stale = (
        db.query(ReversalRequest)
        .filter(
            ReversalRequest.status == "submitting",
            ReversalRequest.lease_expires_at.isnot(None),
            ReversalRequest.lease_expires_at <= now,
        )
        .all()
    )
    for reversal in stale:
        reversal.status = "unknown"
        reversal.response_description = (
            "Reversal worker lease expired after submission began; provider outcome is unknown"
        )
        reversal.lease_owner = None
        reversal.lease_expires_at = None
        audit(
            db,
            organization_id=reversal.organization_id,
            merchant_id=reversal.merchant_account_id,
            action="reversal_submission_abandoned",
            entity_type="reversal_request",
            entity_id=reversal.id,
        )

    query = (
        db.query(ReversalRequest)
        .filter(
            ReversalRequest.status == "approved",
            or_(
                ReversalRequest.lease_owner.is_(None),
                ReversalRequest.lease_expires_at <= now,
            ),
        )
        .order_by(ReversalRequest.approved_at.asc(), ReversalRequest.created_at.asc())
        .limit(min(max(limit, 1), 100))
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    rows = query.all()
    lease_until = now + timedelta(seconds=settings.REVERSAL_LEASE_SECONDS)
    for reversal in rows:
        reversal.lease_owner = worker_id
        reversal.lease_expires_at = lease_until
    db.commit()
    return [reversal.id for reversal in rows]


async def submit_claimed_reversal(
    db: Session,
    reversal_id: str,
    worker_id: str,
) -> ReversalRequest | None:
    query = db.query(ReversalRequest).filter(ReversalRequest.id == reversal_id)
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    reversal = query.first()
    if not reversal or reversal.status != "approved" or reversal.lease_owner != worker_id:
        return None
    payment_query = db.query(Payment).filter(
        Payment.id == reversal.payment_id,
        Payment.organization_id == reversal.organization_id,
        Payment.merchant_account_id == reversal.merchant_account_id,
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        payment_query = payment_query.with_for_update()
    payment = payment_query.first()
    binding_error = reversal_binding_error(reversal, payment)
    if binding_error:
        reversal.status = "failed"
        reversal.response_description = f"Reversal binding validation failed: {binding_error}"
        reversal.completed_at = utcnow()
        reversal.lease_owner = None
        reversal.lease_expires_at = None
        audit(
            db,
            organization_id=reversal.organization_id,
            merchant_id=reversal.merchant_account_id,
            action="reversal_submission_binding_conflict",
            entity_type="reversal_request",
            entity_id=reversal.id,
            metadata={"reason": binding_error},
        )
        db.commit()
        REVERSAL_SUBMISSIONS.labels("binding_conflict").inc()
        return reversal
    merchant = (
        db.query(MerchantAccount).filter(MerchantAccount.id == reversal.merchant_account_id).first()
    )
    credential = (
        db.query(DarajaCredential)
        .filter(
            DarajaCredential.merchant_account_id == reversal.merchant_account_id,
            DarajaCredential.environment == merchant.environment if merchant else False,
            DarajaCredential.is_active.is_(True),
        )
        .first()
    )
    if not merchant or not credential:
        reversal.status = "failed"
        reversal.response_description = "Active merchant reversal credentials are unavailable"
        reversal.completed_at = utcnow()
        reversal.lease_owner = None
        reversal.lease_expires_at = None
        db.commit()
        REVERSAL_SUBMISSIONS.labels("credentials_unavailable").inc()
        return reversal

    try:
        secrets = decrypted_secrets(credential)
        initiator_name, security_credential = decrypted_reversal_credentials(credential)
    except Exception:
        reversal.status = "failed"
        reversal.response_description = "Merchant reversal credentials could not be decrypted"
        reversal.completed_at = utcnow()
        reversal.lease_owner = None
        reversal.lease_expires_at = None
        db.commit()
        REVERSAL_SUBMISSIONS.labels("credentials_unavailable").inc()
        return reversal

    snapshot = {
        "id": reversal.id,
        "organization_id": reversal.organization_id,
        "merchant_account_id": reversal.merchant_account_id,
        "environment": merchant.environment,
        "shortcode": merchant.shortcode,
        "transaction_id": payment.mpesa_receipt_number,
        "amount": reversal.amount,
        "reason": reversal.reason,
        "correlation_id": payment.correlation_id,
    }
    if not snapshot["transaction_id"]:
        reversal.status = "failed"
        reversal.response_description = "The payment has no M-PESA receipt to reverse"
        reversal.completed_at = utcnow()
        reversal.lease_owner = None
        reversal.lease_expires_at = None
        db.commit()
        REVERSAL_SUBMISSIONS.labels("missing_receipt").inc()
        return reversal

    reversal.status = "submitting"
    reversal.submission_started_at = utcnow()
    db.commit()

    base_url = settings.public_url
    result_url = (
        f"{base_url}/api/v1/callbacks/mpesa/reversals/" f"{snapshot['merchant_account_id']}/result"
    )
    timeout_url = (
        f"{base_url}/api/v1/callbacks/mpesa/reversals/" f"{snapshot['merchant_account_id']}/timeout"
    )
    response: dict = {}
    sent_payload: dict = {}
    outcome = "unknown"
    description = "Daraja reversal submission outcome is uncertain"
    try:
        started = time.perf_counter()
        try:
            response, sent_payload = await DarajaClient(
                str(snapshot["environment"])
            ).reverse_transaction(
                secrets=secrets,
                initiator_name=initiator_name,
                security_credential=security_credential,
                shortcode=str(snapshot["shortcode"]),
                transaction_id=str(snapshot["transaction_id"]),
                amount=snapshot["amount"],
                remarks=str(snapshot["reason"]),
                result_url=result_url,
                timeout_url=timeout_url,
                occasion=f"LynxPay reversal {snapshot['id']}",
                correlation_id=str(snapshot["correlation_id"]),
            )
        finally:
            DARAJA_REQUEST_DURATION.labels("reversal", str(snapshot["environment"])).observe(
                time.perf_counter() - started
            )
        outcome = "submitted" if str(response.get("ResponseCode", "")) == "0" else "failed"
        description = (
            response.get("ResponseDescription")
            or response.get("ResponseDesc")
            or "Daraja accepted the reversal for processing"
        )
    except DarajaRequestNotSentError:
        outcome = "failed"
        description = "Daraja reversal request was not submitted"
    except DarajaSubmissionUncertainError:
        outcome = "unknown"
    except Exception:
        outcome = "unknown"

    query = db.query(ReversalRequest).filter(ReversalRequest.id == reversal_id)
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    reversal = query.first()
    if not reversal or reversal.status != "submitting" or reversal.lease_owner != worker_id:
        db.rollback()
        return None
    reversal.status = outcome
    reversal.response_code = (
        str(response["ResponseCode"]) if response.get("ResponseCode") is not None else None
    )
    reversal.response_description = description
    if response:
        merge_reversal_response_evidence(reversal, "submission", response)
    if sent_payload:
        merge_reversal_request_evidence(
            reversal,
            "provider_request",
            redact_reversal_payload(sent_payload),
        )
    reversal.originator_conversation_id = response.get("OriginatorConversationID")
    reversal.conversation_id = response.get("ConversationID")
    reversal.submitted_at = utcnow() if outcome == "submitted" else None
    reversal.completed_at = utcnow() if outcome in {"failed", "unknown"} else None
    reversal.lease_owner = None
    reversal.lease_expires_at = None
    audit(
        db,
        organization_id=reversal.organization_id,
        merchant_id=reversal.merchant_account_id,
        action=f"reversal_{outcome}",
        entity_type="reversal_request",
        entity_id=reversal.id,
        metadata={
            "response_code": reversal.response_code,
            "originator_conversation_id": reversal.originator_conversation_id,
        },
    )
    db.commit()
    REVERSAL_SUBMISSIONS.labels(outcome).inc()
    MPESA_RESULT_CODES.labels("reversal_submission", reversal.response_code or outcome).inc()
    return reversal
