"""Daraja transaction-status reconciliation for missing or ambiguous callbacks."""

from __future__ import annotations

from datetime import timedelta
import time
import uuid

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.datetime_utils import ensure_utc_datetime
from app.daraja import DarajaClient
from app.models import (
    DarajaCredential,
    MerchantAccount,
    Payment,
    PaymentAttempt,
    PaymentStatusCheck,
)
from app.observability import (
    DARAJA_REQUEST_DURATION,
    MERCHANT_PAYMENT_OUTCOMES,
    MPESA_RESULT_CODES,
    PAYMENT_OUTCOMES,
    RECONCILIATION_CHECKS,
)
from app.provider_codes import classify_mpesa_result
from app.service import (
    audit,
    decrypted_secrets,
    queue_webhooks,
    transition_and_record,
    utcnow,
)


def claim_reconciliations(db: Session, worker_id: str, limit: int = 20) -> list[str]:
    now = utcnow()
    query = (
        db.query(Payment)
        .filter(
            Payment.status.in_(["stk_sent", "unknown"]),
            Payment.checkout_request_id.isnot(None),
            Payment.reconciliation_attempts < settings.RECONCILIATION_MAX_ATTEMPTS,
            Payment.next_reconciliation_at.isnot(None),
            Payment.next_reconciliation_at <= now,
            or_(
                Payment.reconciliation_lease_owner.is_(None),
                Payment.reconciliation_lease_expires_at <= now,
            ),
        )
        .order_by(Payment.next_reconciliation_at.asc())
        .limit(min(max(limit, 1), 100))
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    payments = query.all()
    lease_until = now + timedelta(seconds=settings.WEBHOOK_LEASE_SECONDS)
    for payment in payments:
        payment.reconciliation_lease_owner = worker_id
        payment.reconciliation_lease_expires_at = lease_until
    db.commit()
    return [payment.id for payment in payments]


def _active_credential(db: Session, merchant_id: str) -> DarajaCredential | None:
    return (
        db.query(DarajaCredential)
        .filter(
            DarajaCredential.merchant_account_id == merchant_id,
            DarajaCredential.is_active.is_(True),
        )
        .first()
    )


def reconciliation_delay_seconds(payment: Payment, now) -> int | None:
    """Use faster checks early, then preserve Daraja capacity over the 24-hour window."""

    age_seconds = max((now - ensure_utc_datetime(payment.created_at)).total_seconds(), 0)
    if age_seconds < 5 * 60:
        return settings.RECONCILIATION_FREQUENT_INTERVAL_SECONDS
    if age_seconds < 30 * 60:
        return settings.RECONCILIATION_SLOW_INTERVAL_SECONDS
    if age_seconds < settings.RECONCILIATION_MANUAL_REVIEW_AFTER_HOURS * 60 * 60:
        return settings.RECONCILIATION_OCCASIONAL_INTERVAL_SECONDS
    return None


async def reconcile_payment(
    db: Session,
    payment_id: str,
    *,
    worker_id: str | None = None,
) -> PaymentStatusCheck | None:
    # Phase 1: establish a short durable lease and snapshot the immutable
    # provider request evidence. No Daraja network call occurs while a payment
    # row lock or database transaction is held.
    effective_worker_id = worker_id or f"manual-reconcile-{uuid.uuid4()}"
    query = db.query(Payment).filter(Payment.id == payment_id)
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    payment = query.first()
    if (
        not payment
        or payment.status not in {"stk_sent", "unknown"}
        or not payment.checkout_request_id
    ):
        return None
    if worker_id and payment.reconciliation_lease_owner != worker_id:
        return None
    if not worker_id:
        now = utcnow()
        active_other_lease = (
            payment.reconciliation_lease_owner
            and payment.reconciliation_lease_expires_at
            and ensure_utc_datetime(payment.reconciliation_lease_expires_at) > now
        )
        if active_other_lease:
            return None
        payment.reconciliation_lease_owner = effective_worker_id
        payment.reconciliation_lease_expires_at = now + timedelta(
            seconds=settings.WEBHOOK_LEASE_SECONDS
        )
    snapshot = {
        "organization_id": payment.organization_id,
        "merchant_account_id": payment.merchant_account_id,
        "checkout_request_id": payment.checkout_request_id,
        "correlation_id": payment.correlation_id,
    }
    merchant = (
        db.query(MerchantAccount).filter(MerchantAccount.id == payment.merchant_account_id).first()
    )
    credential = _active_credential(db, payment.merchant_account_id)
    environment = merchant.environment if merchant else None
    shortcode = credential.shortcode if credential else None
    if credential:
        db.expunge(credential)
    db.commit()

    outcome = "ambiguous"
    response: dict = {}
    code: str | None = None
    description: str | None = None
    try:
        if not environment or not credential or not shortcode:
            raise RuntimeError("Active merchant credentials are unavailable")
        client = DarajaClient(environment)
        started = time.perf_counter()
        try:
            response, _ = await client.query_stk_status(
                secrets=decrypted_secrets(credential),
                shortcode=shortcode,
                checkout_request_id=snapshot["checkout_request_id"],
                correlation_id=snapshot["correlation_id"],
            )
        finally:
            DARAJA_REQUEST_DURATION.labels("stk_status_query", environment).observe(
                time.perf_counter() - started
            )
        code = str(response["ResultCode"]) if response.get("ResultCode") is not None else None
        description = response.get("ResultDesc") or response.get("ResponseDescription")
    except Exception as exc:
        response = {"transport_error": exc.__class__.__name__}
        description = "Daraja status could not be verified"

    # Phase 2: reacquire the row and apply only if the lease and request
    # evidence still match. A callback may have completed the payment while the
    # provider call was in flight; that success always wins.
    query = db.query(Payment).filter(Payment.id == payment_id)
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    payment = query.first()
    if not payment or payment.reconciliation_lease_owner != effective_worker_id:
        db.rollback()
        return None
    now = utcnow()
    evidence_changed = payment.checkout_request_id != snapshot["checkout_request_id"]
    status_superseded = payment.status not in {"stk_sent", "unknown"}
    payment.reconciliation_attempts += 1
    payment.last_reconciled_at = now
    attempt = (
        db.query(PaymentAttempt)
        .filter(
            PaymentAttempt.payment_id == payment.id,
            PaymentAttempt.checkout_request_id == snapshot["checkout_request_id"],
        )
        .first()
    )

    if evidence_changed or status_superseded:
        outcome = "superseded"
        description = (
            "Payment was completed while reconciliation was in flight"
            if payment.status == "success"
            else "Payment evidence changed while reconciliation was in flight"
        )
    elif code == "0":
        transition_and_record(
            db,
            payment=payment,
            target="success",
            event_type="payment.success",
            details={"source": "daraja_status_query"},
        )
        payment.result_code = code
        payment.result_description = description
        payment.paid_at = now
        payment.success_source = "status_query"
        payment.provider_acceptance_state = "accepted"
        if payment.mpesa_receipt_number:
            payment.receipt_status = "present"
            payment.review_status = "none"
            payment.review_reason = None
        else:
            payment.receipt_status = "missing"
            payment.review_status = "needs_review"
            payment.review_reason = "status_query_success_missing_receipt"
        if attempt:
            attempt.status = "succeeded"
        queue_webhooks(db, payment, "payment.success")
        outcome = "success"
    elif code is not None:
        classified = classify_mpesa_result(code)
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
                details={
                    "source": "daraja_status_query",
                    "result_code": code,
                    "provider_category": classified.category,
                },
            )
        payment.result_code = code
        payment.result_description = description
        payment.failed_at = now if target in {"failed", "timeout"} else None
        payment.provider_acceptance_state = "accepted"
        payment.receipt_status = "not_applicable" if target != "unknown" else "missing"
        payment.review_status = "needs_review" if classified.needs_review else "none"
        payment.review_reason = (
            f"provider_result_{classified.category}" if classified.needs_review else None
        )
        if attempt:
            attempt.status = "uncertain" if target == "unknown" else target
        queue_webhooks(db, payment, f"payment.{target}")
        outcome = target
    else:
        payment.provider_acceptance_state = "uncertain"

    check = PaymentStatusCheck(
        organization_id=snapshot["organization_id"],
        merchant_account_id=snapshot["merchant_account_id"],
        payment_id=payment.id,
        checkout_request_id=snapshot["checkout_request_id"],
        result_code=code,
        result_description=description,
        raw_response=response,
        outcome=outcome,
        checked_at=now,
    )
    db.add(check)
    RECONCILIATION_CHECKS.labels(outcome).inc()
    MPESA_RESULT_CODES.labels("stk_status_query", code or "transport_error").inc()
    if outcome in {"success", "failed", "timeout"}:
        PAYMENT_OUTCOMES.labels(outcome, "status_query").inc()
        MERCHANT_PAYMENT_OUTCOMES.labels(payment.merchant_account_id, outcome, "status_query").inc()
    audit(
        db,
        organization_id=payment.organization_id,
        merchant_id=payment.merchant_account_id,
        action="payment_status_reconciled",
        entity_type="payment",
        entity_id=payment.id,
        metadata={"outcome": outcome, "result_code": code},
    )

    if payment.status in {"stk_sent", "unknown"}:
        next_delay = reconciliation_delay_seconds(payment, now)
        if (
            payment.reconciliation_attempts >= settings.RECONCILIATION_MAX_ATTEMPTS
            or next_delay is None
        ):
            if payment.status == "stk_sent":
                transition_and_record(
                    db,
                    payment=payment,
                    target="unknown",
                    event_type="payment.unknown",
                    details={"source": "reconciliation_exhausted"},
                )
                queue_webhooks(db, payment, "payment.unknown")
            payment.review_status = "needs_review"
            payment.review_reason = "reconciliation_exhausted"
            payment.next_reconciliation_at = None
        else:
            payment.next_reconciliation_at = now + timedelta(seconds=next_delay)
    else:
        payment.next_reconciliation_at = None
    payment.reconciliation_lease_owner = None
    payment.reconciliation_lease_expires_at = None
    db.commit()
    db.refresh(check)
    return check
