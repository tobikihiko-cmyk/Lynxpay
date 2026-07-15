"""Daraja transaction-status reconciliation for missing or ambiguous callbacks."""

from __future__ import annotations

from datetime import timedelta
import time

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
from app.observability import DARAJA_REQUEST_DURATION, PAYMENT_OUTCOMES, RECONCILIATION_CHECKS
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
    merchant = (
        db.query(MerchantAccount).filter(MerchantAccount.id == payment.merchant_account_id).first()
    )
    credential = _active_credential(db, payment.merchant_account_id)
    now = utcnow()
    payment.reconciliation_attempts += 1
    payment.last_reconciled_at = now
    outcome = "ambiguous"
    response: dict = {}
    code: str | None = None
    description: str | None = None
    attempt = (
        db.query(PaymentAttempt)
        .filter(
            PaymentAttempt.payment_id == payment.id,
            PaymentAttempt.checkout_request_id == payment.checkout_request_id,
        )
        .first()
    )

    try:
        if not merchant or not credential:
            raise RuntimeError("Active merchant credentials are unavailable")
        client = DarajaClient(merchant.environment)
        started = time.perf_counter()
        try:
            response, _ = await client.query_stk_status(
                secrets=decrypted_secrets(credential),
                shortcode=credential.shortcode,
                checkout_request_id=payment.checkout_request_id,
            )
        finally:
            DARAJA_REQUEST_DURATION.labels("stk_status_query", merchant.environment).observe(
                time.perf_counter() - started
            )
        code = str(response["ResultCode"]) if response.get("ResultCode") is not None else None
        description = response.get("ResultDesc") or response.get("ResponseDescription")
        if code == "0":
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
            target = "timeout" if payment.status == "stk_sent" and code == "1037" else "failed"
            transition_and_record(
                db,
                payment=payment,
                target=target,
                event_type=f"payment.{target}",
                details={"source": "daraja_status_query", "result_code": code},
            )
            payment.result_code = code
            payment.result_description = description
            payment.failed_at = now
            payment.provider_acceptance_state = "accepted"
            payment.receipt_status = "not_applicable"
            payment.review_status = "none"
            payment.review_reason = None
            if attempt:
                attempt.status = target
            queue_webhooks(db, payment, f"payment.{target}")
            outcome = target
    except Exception as exc:
        response = {"transport_error": exc.__class__.__name__}
        description = "Daraja status could not be verified"
        payment.provider_acceptance_state = "uncertain"

    check = PaymentStatusCheck(
        organization_id=payment.organization_id,
        merchant_account_id=payment.merchant_account_id,
        payment_id=payment.id,
        checkout_request_id=payment.checkout_request_id,
        result_code=code,
        result_description=description,
        raw_response=response,
        outcome=outcome,
        checked_at=now,
    )
    db.add(check)
    RECONCILIATION_CHECKS.labels(outcome).inc()
    if outcome in {"success", "failed", "timeout"}:
        PAYMENT_OUTCOMES.labels(outcome, "status_query").inc()
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
