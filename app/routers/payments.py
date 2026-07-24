"""LynxPay domain HTTP routes."""

from __future__ import annotations

from datetime import datetime, timedelta
import time
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_db
from app.core.security import (
    hash_opaque_token,
)
from app.daraja import (
    DarajaClient,
    DarajaRequestNotSentError,
    redact_stk_payload,
)
from app.deps import (
    Principal,
    require_scope,
    scoped_merchant,
)
from app.models import (
    DarajaCredential,
    MerchantAccount,
    MpesaCallback,
    Payment,
    PaymentAttempt,
    PaymentLedgerEntry,
    PaymentStatusCheck,
    ReversalRequest,
)
from app.observability import (
    DARAJA_REQUEST_DURATION,
    PAYMENTS_CREATED,
    STK_PUSH_FAILED,
    STK_PUSH_SENT,
)
from app.schemas import (
    PaymentRetryRequest,
    StkPushCreate,
)
from app.service import (
    active_credential,
    audit,
    begin_retry_and_record,
    callback_view,
    decrypted_secrets,
    ledger,
    payment_payload,
    queue_webhooks,
    request_fingerprint,
    transition_and_record,
    utcnow,
)

router = APIRouter(tags=["LynxPay"])


def _attempt_view(attempt: PaymentAttempt) -> dict:
    return {
        "id": attempt.id,
        "payment_id": attempt.payment_id,
        "attempt_number": attempt.attempt_number,
        "attempt_type": attempt.attempt_type,
        "status": attempt.status,
        "merchant_request_id": attempt.merchant_request_id,
        "checkout_request_id": attempt.checkout_request_id,
        "response_code": attempt.response_code,
        "response_description": attempt.response_description,
        "submission_started_at": attempt.submission_started_at.isoformat()
        if attempt.submission_started_at
        else None,
        "provider_responded_at": attempt.provider_responded_at.isoformat()
        if attempt.provider_responded_at
        else None,
        "abandoned_at": attempt.abandoned_at.isoformat() if attempt.abandoned_at else None,
        "created_at": attempt.created_at.isoformat() if attempt.created_at else None,
        "updated_at": attempt.updated_at.isoformat() if attempt.updated_at else None,
    }


def _payment_attempt_result(payment: Payment, attempt: PaymentAttempt) -> dict:
    result = payment_payload(payment)
    result["idempotent_replay"] = False
    result["attempt"] = _attempt_view(attempt)
    return result


async def _submit_stk_attempt(
    *,
    db: Session,
    payment: Payment,
    attempt: PaymentAttempt,
    merchant: MerchantAccount,
    credential: DarajaCredential,
    principal: Principal,
    request: Request,
) -> dict:
    """Submit one already-persisted attempt and record its acceptance evidence."""

    client = DarajaClient(merchant.environment)
    started = time.perf_counter()
    try:
        response, sent_payload = await client.stk_push(
            secrets=decrypted_secrets(credential),
            shortcode=credential.shortcode,
            till_number=merchant.till_number,
            shortcode_type=merchant.shortcode_type,
            phone=payment.customer_phone,
            amount=payment.amount,
            external_reference=payment.external_reference,
            description=payment.description,
            callback_url=merchant.callback_url,
            correlation_id=payment.correlation_id,
        )
    except DarajaRequestNotSentError:
        STK_PUSH_FAILED.labels("not_sent").inc()
        attempt.status = "not_sent"
        attempt.provider_responded_at = utcnow()
        attempt.response_description = "Daraja STK request was not submitted"
        payment.provider_acceptance_state = "rejected"
        payment.receipt_status = "not_applicable"
        payment.review_status = "none"
        payment.review_reason = None
        transition_and_record(
            db,
            payment=payment,
            target="failed",
            event_type="payment.failed",
            principal=principal,
            request=request,
            details={"attempt_id": attempt.id, "reason": "provider_request_not_sent"},
        )
        payment.result_description = attempt.response_description
        payment.failed_at = utcnow()
        queue_webhooks(db, payment, "payment.failed")
        audit(
            db,
            organization_id=payment.organization_id,
            merchant_id=merchant.id,
            action="stk_push_request_failed",
            entity_type="payment_attempt",
            entity_id=attempt.id,
            principal=principal,
            request=request,
        )
        db.commit()
        return _payment_attempt_result(payment, attempt)
    except Exception:
        STK_PUSH_FAILED.labels("uncertain").inc()
        attempt.status = "uncertain"
        attempt.provider_responded_at = utcnow()
        attempt.response_description = "Daraja acceptance could not be verified"
        payment.provider_acceptance_state = "uncertain"
        payment.review_status = "needs_review"
        payment.review_reason = "provider_acceptance_uncertain"
        transition_and_record(
            db,
            payment=payment,
            target="unknown",
            event_type="payment.unknown",
            principal=principal,
            request=request,
            details={"attempt_id": attempt.id, "reason": "provider_acceptance_uncertain"},
        )
        payment.result_description = attempt.response_description
        queue_webhooks(db, payment, "payment.unknown")
        db.commit()
        return _payment_attempt_result(payment, attempt)
    finally:
        DARAJA_REQUEST_DURATION.labels("stk_push", merchant.environment).observe(
            time.perf_counter() - started
        )

    attempt.request_payload_redacted = redact_stk_payload(sent_payload)
    attempt.response_payload = response
    attempt.provider_responded_at = utcnow()
    attempt.merchant_request_id = response.get("MerchantRequestID")
    attempt.checkout_request_id = response.get("CheckoutRequestID")
    attempt.response_code = str(response.get("ResponseCode", ""))
    attempt.response_description = response.get("ResponseDescription") or response.get(
        "CustomerMessage"
    )
    if attempt.response_code != "0":
        STK_PUSH_FAILED.labels("provider_rejected").inc()
        attempt.status = "rejected"
        payment.provider_acceptance_state = "rejected"
        payment.receipt_status = "not_applicable"
        payment.review_status = "none"
        payment.review_reason = None
        transition_and_record(
            db,
            payment=payment,
            target="failed",
            event_type="payment.failed",
            principal=principal,
            request=request,
            details={"attempt_id": attempt.id, "response_code": attempt.response_code},
        )
        payment.result_code = attempt.response_code
        payment.result_description = attempt.response_description
        payment.failed_at = utcnow()
        queue_webhooks(db, payment, "payment.failed")
        db.commit()
        return _payment_attempt_result(payment, attempt)
    if not attempt.checkout_request_id:
        STK_PUSH_FAILED.labels("missing_checkout_request_id").inc()
        attempt.status = "uncertain"
        payment.provider_acceptance_state = "uncertain"
        payment.review_status = "needs_review"
        payment.review_reason = "accepted_without_checkout_request_id"
        transition_and_record(
            db,
            payment=payment,
            target="unknown",
            event_type="payment.unknown",
            principal=principal,
            request=request,
            details={"attempt_id": attempt.id, "reason": "missing_checkout_request_id"},
        )
        payment.result_description = "Daraja accepted the request without a CheckoutRequestID"
        queue_webhooks(db, payment, "payment.unknown")
        db.commit()
        return _payment_attempt_result(payment, attempt)

    payment.checkout_request_id = attempt.checkout_request_id
    payment.merchant_request_id = attempt.merchant_request_id
    payment.provider_acceptance_state = "accepted"
    payment.receipt_status = "missing"
    payment.review_status = "none"
    payment.review_reason = None
    payment.next_reconciliation_at = utcnow() + timedelta(
        seconds=settings.RECONCILIATION_INITIAL_DELAY_SECONDS
    )
    attempt.status = "accepted"
    transition_and_record(
        db,
        payment=payment,
        target="stk_sent",
        event_type="payment.stk_sent",
        principal=principal,
        request=request,
        details={"attempt_id": attempt.id, "checkout_request_id": attempt.checkout_request_id},
    )
    audit(
        db,
        organization_id=payment.organization_id,
        merchant_id=merchant.id,
        action="stk_push_initiated",
        entity_type="payment_attempt",
        entity_id=attempt.id,
        principal=principal,
        request=request,
        metadata={
            "checkout_request_id": attempt.checkout_request_id,
            "attempt_number": attempt.attempt_number,
            "attempt_type": attempt.attempt_type,
        },
    )
    queue_webhooks(db, payment, "payment.stk_sent")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        locked = db.query(Payment).filter(Payment.id == payment.id).with_for_update().one()
        locked.provider_acceptance_state = "uncertain"
        locked.review_status = "needs_review"
        locked.review_reason = "checkout_request_id_conflict"
        if locked.status == "pending":
            transition_and_record(
                db,
                payment=locked,
                target="unknown",
                event_type="payment.unknown",
                principal=principal,
                request=request,
                details={"reason": "checkout_request_id_conflict"},
            )
        audit(
            db,
            organization_id=locked.organization_id,
            merchant_id=locked.merchant_account_id,
            action="checkout_request_id_conflict",
            entity_type="payment",
            entity_id=locked.id,
            principal=principal,
            request=request,
        )
        db.commit()
        raise HTTPException(
            status_code=409, detail="Daraja CheckoutRequestID has already been recorded"
        ) from None
    db.refresh(payment)
    STK_PUSH_SENT.inc()
    return _payment_attempt_result(payment, attempt)


@router.post("/payments/stk-push", status_code=201)
async def create_stk_push(
    payload: StkPushCreate,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=255),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:write")),
):
    merchant = scoped_merchant(db, principal, payload.merchant_id)
    is_merchant_verification = payload.purpose == "merchant_verification"
    if is_merchant_verification and not principal.is_control_plane_admin:
        raise HTTPException(
            status_code=403,
            detail="Merchant verification payments require administrator authentication",
        )
    required_status = "verified" if is_merchant_verification else "active"
    if merchant.status != required_status:
        raise HTTPException(
            status_code=409,
            detail=(
                "Merchant credentials must be verified before the KES 1 test payment"
                if is_merchant_verification
                else "Merchant must be active before initiating STK Push"
            ),
        )
    credential = active_credential(db, merchant)
    request_data = payload.model_dump(mode="json")
    fingerprint = request_fingerprint(request_data)
    idempotency_digest = (
        hash_opaque_token(f"idempotency:{idempotency_key}") if idempotency_key else None
    )
    duplicate_query = db.query(Payment).filter(
        Payment.merchant_account_id == merchant.id,
        Payment.external_reference == payload.external_reference,
    )
    existing = duplicate_query.first()
    if not existing and idempotency_key:
        existing = (
            db.query(Payment)
            .filter(
                Payment.merchant_account_id == merchant.id,
                Payment.idempotency_key.in_([idempotency_digest, idempotency_key]),
            )
            .first()
        )
    if existing:
        if existing.idempotency_request_hash == fingerprint:
            replay = payment_payload(existing)
            replay["idempotent_replay"] = True
            return replay
        raise HTTPException(
            status_code=409,
            detail="Payment reference or idempotency key was reused with different data",
        )

    payment = Payment(
        organization_id=merchant.organization_id,
        merchant_account_id=merchant.id,
        external_reference=payload.external_reference,
        idempotency_key=idempotency_digest,
        idempotency_request_hash=fingerprint,
        order_id=payload.order_id,
        invoice_id=payload.invoice_id,
        customer_name=payload.customer_name,
        customer_phone=payload.phone_number,
        amount=payload.amount,
        currency="KES",
        description=payload.description,
        purpose=payload.purpose,
        callback_metadata=payload.callback_metadata,
        correlation_id=request.state.request_id,
        status="created",
    )
    db.add(payment)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Duplicate payment reference or idempotency key"
        ) from None
    ledger(db, payment=payment, event_type="payment.created", status_from=None)
    audit(
        db,
        organization_id=payment.organization_id,
        merchant_id=merchant.id,
        action="payment_created",
        entity_type="payment",
        entity_id=payment.id,
        principal=principal,
        request=request,
        metadata={
            "external_reference": payment.external_reference,
            "amount": str(payment.amount),
            "purpose": payment.purpose,
        },
    )
    transition_and_record(
        db,
        payment=payment,
        target="pending",
        event_type="payment.pending",
        principal=principal,
        request=request,
    )
    attempt = PaymentAttempt(
        payment_id=payment.id,
        merchant_account_id=merchant.id,
        attempt_number=1,
        phone_number=payment.customer_phone,
        amount=payment.amount,
        request_payload_redacted={
            "phone_number": f"{payment.customer_phone[:6]}***{payment.customer_phone[-3:]}",
            "amount": str(payment.amount),
            "external_reference": payment.external_reference,
        },
        status="submitting",
        submission_started_at=utcnow(),
        attempt_type="initial",
        initiated_by_user_id=principal.user_id,
        initiated_by_api_key_id=principal.api_key_id,
    )
    db.add(attempt)
    db.commit()
    PAYMENTS_CREATED.inc()
    return await _submit_stk_attempt(
        db=db,
        payment=payment,
        attempt=attempt,
        merchant=merchant,
        credential=credential,
        principal=principal,
        request=request,
    )


def _payments_query(db: Session, principal: Principal):
    query = db.query(Payment).filter(Payment.organization_id == principal.organization_id)
    if principal.merchant_id:
        query = query.filter(Payment.merchant_account_id == principal.merchant_id)
    elif principal.api_key_id and principal.environment:
        query = query.join(
            MerchantAccount, MerchantAccount.id == Payment.merchant_account_id
        ).filter(MerchantAccount.environment == principal.environment)
    return query


@router.get("/payments")
def list_payments(
    merchant_id: str | None = None,
    status: str | None = None,
    purpose: Literal["payment", "merchant_verification"] | None = None,
    environment: str | None = None,
    review_status: str | None = None,
    search: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    before: datetime | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:read")),
):
    query = _payments_query(db, principal)
    if merchant_id:
        scoped_merchant(db, principal, merchant_id)
        query = query.filter(Payment.merchant_account_id == merchant_id)
    if status:
        query = query.filter(Payment.status == status)
    if purpose:
        query = query.filter(Payment.purpose == purpose)
    if environment:
        query = query.filter(Payment.merchant.has(environment=environment))
    if review_status:
        query = query.filter(Payment.review_status == review_status)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Payment.external_reference.ilike(term),
                Payment.customer_phone.ilike(term),
                Payment.mpesa_receipt_number.ilike(term),
                Payment.checkout_request_id.ilike(term),
            )
        )
    if created_from:
        query = query.filter(Payment.created_at >= created_from)
    if created_to:
        query = query.filter(Payment.created_at <= created_to)
    if before:
        query = query.filter(Payment.created_at < before)
    page_size = min(max(limit, 1), 500)
    records = query.order_by(Payment.created_at.desc()).limit(page_size).all()
    return {
        "items": [payment_payload(item) for item in records],
        "next_before": records[-1].created_at.isoformat() if len(records) == page_size else None,
    }


@router.get("/payments/{payment_id}")
def get_payment(
    payment_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:read")),
):
    payment = _payments_query(db, principal).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment_payload(payment)


@router.get("/payments/{payment_id}/attempts")
def list_payment_attempts(
    payment_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:read")),
):
    payment = _payments_query(db, principal).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    rows = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.payment_id == payment.id)
        .order_by(PaymentAttempt.attempt_number.asc())
        .all()
    )
    return {"items": [_attempt_view(row) for row in rows]}


@router.get("/payments/{payment_id}/timeline")
def get_payment_timeline(
    payment_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:read")),
):
    payment = _payments_query(db, principal).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    attempts = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.payment_id == payment.id)
        .order_by(PaymentAttempt.attempt_number.asc())
        .all()
    )
    callbacks = (
        db.query(MpesaCallback)
        .filter(MpesaCallback.payment_id == payment.id)
        .order_by(MpesaCallback.received_at.asc())
        .all()
    )
    ledger_rows = (
        db.query(PaymentLedgerEntry)
        .filter(PaymentLedgerEntry.payment_id == payment.id)
        .order_by(PaymentLedgerEntry.created_at.asc())
        .all()
    )
    status_checks = (
        db.query(PaymentStatusCheck)
        .filter(PaymentStatusCheck.payment_id == payment.id)
        .order_by(PaymentStatusCheck.checked_at.asc())
        .all()
    )
    reversals = (
        db.query(ReversalRequest)
        .filter(ReversalRequest.payment_id == payment.id)
        .order_by(ReversalRequest.created_at.asc())
        .all()
    )
    return {
        "payment": payment_payload(payment),
        "attempts": [_attempt_view(row) for row in attempts],
        "callbacks": [callback_view(row) for row in callbacks],
        "ledger": [
            {
                "id": row.id,
                "event_type": row.event_type,
                "status_from": row.status_from,
                "status_to": row.status_to,
                "details": row.details,
                "created_at": row.created_at.isoformat(),
            }
            for row in ledger_rows
        ],
        "status_checks": [
            {
                "id": row.id,
                "outcome": row.outcome,
                "result_code": row.result_code,
                "result_description": row.result_description,
                "checked_at": row.checked_at.isoformat(),
            }
            for row in status_checks
        ],
        "reversals": [
            {
                "id": row.id,
                "status": row.status,
                "amount": str(row.amount),
                "currency": row.currency,
                "reason": row.reason,
                "requested_by_user_id": row.requested_by_user_id,
                "approved_by_user_id": row.approved_by_user_id,
                "response_code": row.response_code,
                "response_description": row.response_description,
                "created_at": row.created_at.isoformat(),
                "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            }
            for row in reversals
        ],
    }


@router.post("/payments/{payment_id}/retry")
async def retry_payment(
    payment_id: str,
    payload: PaymentRetryRequest,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:write")),
):
    payment = (
        _payments_query(db, principal).filter(Payment.id == payment_id).with_for_update().first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.status not in {"failed", "timeout", "unknown"}:
        raise HTTPException(
            status_code=409, detail=f"Payment cannot be retried from {payment.status}"
        )
    if payment.mpesa_receipt_number or payment.receipt_status in {"present", "enriched_later"}:
        raise HTTPException(
            status_code=409, detail="Successful receipt evidence blocks payment retry"
        )
    if payment.status == "failed" and payment.provider_acceptance_state != "rejected":
        raise HTTPException(
            status_code=409,
            detail="Only a failed request that Daraja definitely rejected or never received can be retried",
        )
    if payment.status in {"timeout", "unknown"}:
        if not payload.allow_uncertain:
            raise HTTPException(
                status_code=409,
                detail="Uncertain payment retry requires an explicit operator override",
            )
        if not principal.is_control_plane_admin:
            raise HTTPException(
                status_code=403,
                detail="Uncertain payment retry requires organization administrator access",
            )

    merchant = scoped_merchant(db, principal, payment.merchant_account_id)
    required_status = "verified" if payment.purpose == "merchant_verification" else "active"
    if merchant.status != required_status:
        raise HTTPException(status_code=409, detail="Merchant is not eligible for this retry")
    credential = active_credential(db, merchant)
    attempt_number = (
        db.query(PaymentAttempt).filter(PaymentAttempt.payment_id == payment.id).count() + 1
    )
    previous_checkout_request_id = payment.checkout_request_id
    begin_retry_and_record(
        db,
        payment=payment,
        principal=principal,
        request=request,
        attempt_number=attempt_number,
        reason=payload.reason,
    )
    payment.checkout_request_id = None
    payment.merchant_request_id = None
    payment.result_code = None
    payment.result_description = None
    payment.failed_at = None
    payment.next_reconciliation_at = None
    payment.provider_acceptance_state = "not_sent"
    payment.receipt_status = "missing"
    payment.review_status = "none"
    payment.review_reason = None
    attempt = PaymentAttempt(
        payment_id=payment.id,
        merchant_account_id=merchant.id,
        attempt_number=attempt_number,
        phone_number=payment.customer_phone,
        amount=payment.amount,
        request_payload_redacted={
            "phone_number": f"{payment.customer_phone[:6]}***{payment.customer_phone[-3:]}",
            "amount": str(payment.amount),
            "external_reference": payment.external_reference,
        },
        status="created",
        attempt_type="retry",
        retry_reason=payload.reason,
        initiated_by_user_id=principal.user_id,
        initiated_by_api_key_id=principal.api_key_id,
    )
    db.add(attempt)
    db.flush()
    audit(
        db,
        organization_id=payment.organization_id,
        merchant_id=merchant.id,
        action="payment_retry_attempt_created",
        entity_type="payment_attempt",
        entity_id=attempt.id,
        principal=principal,
        request=request,
        metadata={
            "attempt_number": attempt_number,
            "previous_checkout_request_id": previous_checkout_request_id,
        },
    )
    db.commit()
    return await _submit_stk_attempt(
        db=db,
        payment=payment,
        attempt=attempt,
        merchant=merchant,
        credential=credential,
        principal=principal,
        request=request,
    )
