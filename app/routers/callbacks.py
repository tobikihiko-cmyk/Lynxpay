"""LynxPay domain HTTP routes."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.callback_security import callback_allowed, read_callback_body
from app.core.config import settings
from app.core.datetime_utils import ensure_utc_datetime
from app.core.deps import get_client_ip, get_db
from app.database import set_resource_context, set_tenant_context
from app.deps import (
    Principal,
    require_scope,
    scoped_merchant,
)
from app.models import (
    MerchantAccount,
    MpesaCallback,
    Payment,
    PaymentAttempt,
)
from app.observability import (
    CALLBACK_LATENCY,
    CALLBACKS_DUPLICATE,
    CALLBACKS_PROCESSED,
    CALLBACKS_RECEIVED,
    MERCHANT_PAYMENT_OUTCOMES,
    MPESA_RESULT_CODES,
    PAYMENT_OUTCOMES,
)
from app.provider_codes import classify_mpesa_result
from app.schemas import (
    normalize_kenyan_phone,
)
from app.service import (
    audit,
    callback_fields,
    callback_matches_payment,
    callback_view,
    queue_webhooks,
    transition_and_record,
    utcnow,
)
from app.state_machine import InvalidPaymentTransitionError

router = APIRouter(tags=["LynxPay"])


def _normalized_callback_evidence(fields: dict) -> tuple[Decimal | None, str | None]:
    amount = None
    if fields["amount"] is not None:
        with suppress(InvalidOperation, ValueError):
            amount = Decimal(str(fields["amount"])).quantize(Decimal("0.01"))
    phone = None
    if fields["phone"] is not None:
        with suppress(ValueError):
            phone = normalize_kenyan_phone(fields["phone"])
    return amount, phone


@router.post("/callbacks/mpesa/{merchant_id}")
async def receive_mpesa_callback(merchant_id: str, request: Request, db: Session = Depends(get_db)):
    set_resource_context(db, "merchant_id", merchant_id)
    merchant = db.query(MerchantAccount).filter(MerchantAccount.id == merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")
    set_tenant_context(db, merchant.organization_id)
    raw, oversized = await read_callback_body(request)
    request.state.lynxpay_raw_callback = raw
    raw_text = raw.decode("utf-8", errors="replace")
    if oversized:
        payload = {"_truncated": raw_text, "_limit_bytes": settings.MAX_CALLBACK_BODY_BYTES}
    else:
        try:
            payload = json.loads(raw_text)
            if not isinstance(payload, dict):
                payload = {"_value": payload}
        except json.JSONDecodeError:
            payload = {"_unparsed": raw_text}
    fields = callback_fields(payload)
    callback_amount, callback_phone = _normalized_callback_evidence(fields)
    callback = MpesaCallback(
        merchant_account_id=merchant.id,
        checkout_request_id=fields["checkout_request_id"],
        merchant_request_id=fields["merchant_request_id"],
        mpesa_receipt_number=fields["mpesa_receipt_number"],
        callback_amount=callback_amount,
        callback_phone=callback_phone,
        result_code=fields["result_code"],
        result_description=fields["result_description"],
        raw_payload=payload,
        raw_body=raw_text,
        processed=False,
        processing_status="received",
        source_ip=get_client_ip(request),
    )
    db.add(callback)
    db.flush()
    audit(
        db,
        organization_id=merchant.organization_id,
        merchant_id=merchant.id,
        action="callback_received",
        entity_type="mpesa_callback",
        entity_id=callback.id,
        request=request,
        metadata={
            "checkout_request_id": fields["checkout_request_id"],
            "result_code": fields["result_code"],
        },
    )
    db.commit()  # Preserve the raw callback before any validation or payment mutation.
    CALLBACKS_RECEIVED.inc()
    # PostgreSQL set_config(..., true) is transaction-local; restore it after
    # the raw-first durability boundary before touching RLS-protected payment data.
    set_tenant_context(db, merchant.organization_id)

    if oversized:
        callback.processing_status = "oversized"
        callback.processed = True
        callback.processed_at = utcnow()
        audit(
            db,
            organization_id=merchant.organization_id,
            merchant_id=merchant.id,
            action="callback_oversized_rejected",
            entity_type="mpesa_callback",
            entity_id=callback.id,
            request=request,
            metadata={"limit_bytes": settings.MAX_CALLBACK_BODY_BYTES},
        )
        db.commit()
        CALLBACKS_PROCESSED.labels("oversized").inc()
        raise HTTPException(status_code=413, detail="M-PESA callback body is too large")

    if not callback_allowed(request):
        callback.processing_status = "source_rejected"
        callback.processed = True
        callback.processed_at = utcnow()
        audit(
            db,
            organization_id=merchant.organization_id,
            merchant_id=merchant.id,
            action="callback_source_rejected",
            entity_type="mpesa_callback",
            entity_id=callback.id,
            request=request,
        )
        db.commit()
        CALLBACKS_PROCESSED.labels("source_rejected").inc()
        return {"ResultCode": 0, "ResultDesc": "Accepted"}
    if not fields["checkout_request_id"] or fields["result_code"] is None:
        callback.processing_status = "malformed"
        callback.processed = True
        callback.processed_at = utcnow()
        audit(
            db,
            organization_id=merchant.organization_id,
            merchant_id=merchant.id,
            action="callback_malformed",
            entity_type="mpesa_callback",
            entity_id=callback.id,
            request=request,
        )
        db.commit()
        CALLBACKS_PROCESSED.labels("malformed").inc()
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    matched_attempt = (
        db.query(PaymentAttempt)
        .filter(
            PaymentAttempt.merchant_account_id == merchant.id,
            PaymentAttempt.checkout_request_id == fields["checkout_request_id"],
        )
        .first()
    )
    payment_query = db.query(Payment).filter(Payment.merchant_account_id == merchant.id)
    if matched_attempt:
        payment_query = payment_query.filter(Payment.id == matched_attempt.payment_id)
    else:
        payment_query = payment_query.filter(
            Payment.checkout_request_id == fields["checkout_request_id"]
        )
    payment = payment_query.with_for_update().first()
    if not payment:
        callback.processing_status = "unmatched"
        callback.processed = True
        callback.processed_at = utcnow()
        audit(
            db,
            organization_id=merchant.organization_id,
            merchant_id=merchant.id,
            action="callback_unmatched",
            entity_type="mpesa_callback",
            entity_id=callback.id,
            request=request,
        )
        db.commit()
        CALLBACKS_PROCESSED.labels("unmatched").inc()
        return {"ResultCode": 0, "ResultDesc": "Accepted"}
    callback.payment_id = payment.id

    if fields["result_code"] == "0" and fields["mpesa_receipt_number"]:
        previous = (
            db.query(MpesaCallback)
            .filter(
                MpesaCallback.id != callback.id,
                MpesaCallback.merchant_account_id == merchant.id,
                MpesaCallback.checkout_request_id == fields["checkout_request_id"],
                MpesaCallback.mpesa_receipt_number == fields["mpesa_receipt_number"],
                MpesaCallback.callback_amount == callback_amount,
                MpesaCallback.callback_phone == callback_phone,
                MpesaCallback.processing_status == "processed_success",
            )
            .order_by(MpesaCallback.received_at.asc())
            .first()
        )
        if previous:
            callback.duplicate_of_callback_id = previous.id
            callback.processing_status = "duplicate"
            callback.processed = True
            callback.processed_at = utcnow()
            audit(
                db,
                organization_id=merchant.organization_id,
                merchant_id=merchant.id,
                action="duplicate_callback_detected",
                entity_type="mpesa_callback",
                entity_id=callback.id,
                request=request,
                metadata={"duplicate_of": previous.id},
            )
            db.commit()
            CALLBACKS_DUPLICATE.inc()
            CALLBACKS_PROCESSED.labels("duplicate").inc()
            return {"ResultCode": 0, "ResultDesc": "Accepted"}
    elif fields["result_code"] != "0":
        previous = (
            db.query(MpesaCallback)
            .filter(
                MpesaCallback.id != callback.id,
                MpesaCallback.merchant_account_id == merchant.id,
                MpesaCallback.checkout_request_id == fields["checkout_request_id"],
                MpesaCallback.result_code == fields["result_code"],
                MpesaCallback.result_description == fields["result_description"],
                MpesaCallback.processing_status.in_(["processed_failure", "processed_unknown"]),
            )
            .order_by(MpesaCallback.received_at.asc())
            .first()
        )
        if previous:
            callback.duplicate_of_callback_id = previous.id
            callback.processing_status = "duplicate"
            callback.processed = True
            callback.processed_at = utcnow()
            audit(
                db,
                organization_id=merchant.organization_id,
                merchant_id=merchant.id,
                action="duplicate_callback_detected",
                entity_type="mpesa_callback",
                entity_id=callback.id,
                request=request,
                metadata={"duplicate_of": previous.id},
            )
            db.commit()
            CALLBACKS_DUPLICATE.inc()
            CALLBACKS_PROCESSED.labels("duplicate").inc()
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

    event_type = None
    try:
        if fields["result_code"] == "0":
            duplicate_receipt = (
                db.query(Payment)
                .filter(
                    Payment.merchant_account_id == merchant.id,
                    Payment.mpesa_receipt_number == fields["mpesa_receipt_number"],
                    Payment.id != payment.id,
                )
                .first()
                if fields["mpesa_receipt_number"]
                else None
            )
            if duplicate_receipt:
                callback.processing_status = "conflict"
                payment.review_status = "needs_review"
                payment.review_reason = "duplicate_mpesa_receipt"
                if matched_attempt:
                    matched_attempt.status = "conflict"
                audit(
                    db,
                    organization_id=merchant.organization_id,
                    merchant_id=merchant.id,
                    action="duplicate_mpesa_receipt_detected",
                    entity_type="mpesa_callback",
                    entity_id=callback.id,
                    request=request,
                    metadata={"existing_payment_id": duplicate_receipt.id},
                )
            elif (
                payment.status == "success"
                and payment.mpesa_receipt_number == fields["mpesa_receipt_number"]
            ):
                callback.duplicate_of_callback_id = (
                    db.query(MpesaCallback.id)
                    .filter(
                        MpesaCallback.payment_id == payment.id, MpesaCallback.processed.is_(True)
                    )
                    .order_by(MpesaCallback.received_at.asc())
                    .scalar()
                )
                callback.processing_status = "duplicate"
                audit(
                    db,
                    organization_id=merchant.organization_id,
                    merchant_id=merchant.id,
                    action="duplicate_callback_detected",
                    entity_type="mpesa_callback",
                    entity_id=callback.id,
                    request=request,
                )
            elif payment.status == "success" and payment.mpesa_receipt_number:
                callback.processing_status = "conflict"
                payment.review_status = "needs_review"
                payment.review_reason = "conflicting_success_receipt"
                if matched_attempt:
                    matched_attempt.status = "conflict"
                audit(
                    db,
                    organization_id=merchant.organization_id,
                    merchant_id=merchant.id,
                    action="callback_conflict_detected",
                    entity_type="mpesa_callback",
                    entity_id=callback.id,
                    request=request,
                    metadata={
                        "existing_receipt": payment.mpesa_receipt_number,
                        "received_receipt": fields["mpesa_receipt_number"],
                    },
                )
            elif payment.status == "success" and not payment.mpesa_receipt_number:
                matches, reason = callback_matches_payment(payment, fields)
                if matches:
                    payment.mpesa_receipt_number = fields["mpesa_receipt_number"]
                    payment.result_code = fields["result_code"]
                    payment.result_description = fields["result_description"]
                    payment.success_source = "callback"
                    payment.receipt_status = "enriched_later"
                    payment.review_status = "resolved"
                    payment.review_reason = None
                    payment.provider_acceptance_state = "accepted"
                    if matched_attempt:
                        matched_attempt.status = "succeeded"
                    callback.processing_status = "processed_success"
                    audit(
                        db,
                        organization_id=merchant.organization_id,
                        merchant_id=merchant.id,
                        action="payment_success_evidence_enriched",
                        entity_type="payment",
                        entity_id=payment.id,
                        request=request,
                        metadata={
                            "callback_id": callback.id,
                            "receipt": fields["mpesa_receipt_number"],
                        },
                    )
                else:
                    callback.processing_status = "verification_failed"
                    audit(
                        db,
                        organization_id=merchant.organization_id,
                        merchant_id=merchant.id,
                        action="callback_verification_failed",
                        entity_type="mpesa_callback",
                        entity_id=callback.id,
                        request=request,
                        metadata={"reason": reason},
                    )
            else:
                matches, reason = callback_matches_payment(payment, fields)
                if matches:
                    transition_and_record(
                        db,
                        payment=payment,
                        target="success",
                        event_type="payment.success",
                        request=request,
                        details={
                            "callback_id": callback.id,
                            "receipt": fields["mpesa_receipt_number"],
                        },
                    )
                    payment.mpesa_receipt_number = fields["mpesa_receipt_number"]
                    payment.result_code = fields["result_code"]
                    payment.result_description = fields["result_description"]
                    payment.paid_at = utcnow()
                    payment.success_source = "callback"
                    payment.receipt_status = "present"
                    payment.review_status = "none"
                    payment.review_reason = None
                    payment.provider_acceptance_state = "accepted"
                    if matched_attempt:
                        matched_attempt.status = "succeeded"
                    event_type = "payment.success"
                    callback.processing_status = "processed_success"
                else:
                    if payment.status == "stk_sent":
                        transition_and_record(
                            db,
                            payment=payment,
                            target="unknown",
                            event_type="payment.unknown",
                            request=request,
                            details={"callback_id": callback.id, "reason": reason},
                        )
                        event_type = "payment.unknown"
                    payment.review_status = "needs_review"
                    payment.review_reason = reason
                    payment.provider_acceptance_state = "accepted"
                    callback.processing_status = "verification_failed"
                    audit(
                        db,
                        organization_id=merchant.organization_id,
                        merchant_id=merchant.id,
                        action="callback_verification_failed",
                        entity_type="mpesa_callback",
                        entity_id=callback.id,
                        request=request,
                        metadata={"reason": reason},
                    )
        else:
            classified = classify_mpesa_result(fields["result_code"])
            target = (
                "failed"
                if payment.status == "unknown" and classified.target == "timeout"
                else classified.target
            )
            if target != "unknown" or payment.status != "unknown":
                transition_and_record(
                    db,
                    payment=payment,
                    target=target,
                    event_type=f"payment.{target}",
                    request=request,
                    details={
                        "callback_id": callback.id,
                        "result_code": fields["result_code"],
                        "provider_category": classified.category,
                    },
                )
            payment.result_code = fields["result_code"]
            payment.result_description = fields["result_description"]
            payment.failed_at = utcnow() if target in {"failed", "timeout"} else None
            payment.provider_acceptance_state = "accepted"
            payment.receipt_status = "not_applicable" if target != "unknown" else "missing"
            payment.review_status = "needs_review" if classified.needs_review else "none"
            payment.review_reason = (
                f"provider_result_{classified.category}" if classified.needs_review else None
            )
            if matched_attempt:
                matched_attempt.status = "uncertain" if target == "unknown" else target
            event_type = f"payment.{target}"
            callback.processing_status = (
                "processed_unknown" if target == "unknown" else "processed_failure"
            )
    except InvalidPaymentTransitionError as exc:
        callback.processing_status = "conflict"
        payment.review_status = "needs_review"
        payment.review_reason = "callback_transition_conflict"
        if matched_attempt:
            matched_attempt.status = "conflict"
        audit(
            db,
            organization_id=merchant.organization_id,
            merchant_id=merchant.id,
            action="callback_transition_blocked",
            entity_type="mpesa_callback",
            entity_id=callback.id,
            request=request,
            metadata={"reason": str(exc), "payment_status": payment.status},
        )
    callback.processed = True
    callback.processed_at = utcnow()
    if event_type:
        queue_webhooks(db, payment, event_type)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        audit(
            db,
            organization_id=merchant.organization_id,
            merchant_id=merchant.id,
            action="callback_unique_constraint_blocked",
            entity_type="mpesa_callback",
            entity_id=callback.id,
            request=request,
        )
        db.commit()
    CALLBACKS_PROCESSED.labels(callback.processing_status).inc()
    MPESA_RESULT_CODES.labels("stk_callback", fields["result_code"] or "missing").inc()
    if event_type:
        outcome = event_type.removeprefix("payment.")
        PAYMENT_OUTCOMES.labels(outcome, "callback").inc()
        MERCHANT_PAYMENT_OUTCOMES.labels(merchant.id, outcome, "callback").inc()
        CALLBACK_LATENCY.labels(outcome).observe(
            max(
                (utcnow() - ensure_utc_datetime(payment.created_at)).total_seconds(),
                0,
            )
        )
    return {"ResultCode": 0, "ResultDesc": "Accepted"}


def _callbacks_query(db: Session, principal: Principal):
    query = (
        db.query(MpesaCallback)
        .join(MerchantAccount, MerchantAccount.id == MpesaCallback.merchant_account_id)
        .filter(MerchantAccount.organization_id == principal.organization_id)
    )
    if principal.merchant_id:
        query = query.filter(MpesaCallback.merchant_account_id == principal.merchant_id)
    elif principal.api_key_id and principal.environment:
        query = query.filter(MerchantAccount.environment == principal.environment)
    return query


@router.get("/callbacks")
def list_callbacks(
    merchant_id: str | None = None,
    processing_status: str | None = None,
    before: datetime | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("callbacks:read")),
):
    query = _callbacks_query(db, principal)
    if merchant_id:
        scoped_merchant(db, principal, merchant_id)
        query = query.filter(MpesaCallback.merchant_account_id == merchant_id)
    if processing_status:
        query = query.filter(MpesaCallback.processing_status == processing_status)
    if before:
        query = query.filter(MpesaCallback.received_at < before)
    page_size = min(max(limit, 1), 500)
    records = query.order_by(MpesaCallback.received_at.desc()).limit(page_size).all()
    include_raw = bool(
        principal.user_id
        and "callbacks:read_raw" in principal.scopes
        and (not settings.REQUIRE_PRIVILEGED_MFA or principal.mfa_authenticated)
    )
    return {
        "items": [callback_view(item, include_raw=include_raw) for item in records],
        "next_before": records[-1].received_at.isoformat() if len(records) == page_size else None,
    }


@router.get("/callbacks/{callback_id}")
def get_callback(
    callback_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("callbacks:read")),
):
    callback = _callbacks_query(db, principal).filter(MpesaCallback.id == callback_id).first()
    if not callback:
        raise HTTPException(status_code=404, detail="Callback not found")
    include_raw = bool(
        principal.user_id
        and "callbacks:read_raw" in principal.scopes
        and (not settings.REQUIRE_PRIVILEGED_MFA or principal.mfa_authenticated)
    )
    return callback_view(callback, include_raw=include_raw)
