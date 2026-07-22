"""Short, database-only recovery tasks for payment-critical invariants."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import MpesaCallback, Payment, PaymentAttempt, PaymentStatusCheck, WebhookDelivery
from app.service import audit, queue_webhooks, transition_and_record, utcnow


def abandon_stale_stk_submissions(db: Session, limit: int | None = None) -> list[str]:
    """Move stale `submitting` attempts into explicit manual-review state.

    This job never retries an STK request. A crash after the network write can
    make provider acceptance unknowable, so the safe response is to abandon the
    attempt and surface the payment for reconciliation/operator review.
    """

    now = utcnow()
    cutoff = now - timedelta(seconds=settings.STK_SUBMITTING_TIMEOUT_SECONDS)
    query = (
        db.query(PaymentAttempt)
        .filter(
            PaymentAttempt.status == "submitting",
            PaymentAttempt.submission_started_at.isnot(None),
            PaymentAttempt.submission_started_at <= cutoff,
        )
        .order_by(PaymentAttempt.submission_started_at.asc())
        .limit(min(max(limit or settings.MAINTENANCE_BATCH_SIZE, 1), 500))
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    attempts = query.all()
    recovered: list[str] = []
    for attempt in attempts:
        payment = (
            db.query(Payment).filter(Payment.id == attempt.payment_id).with_for_update().first()
        )
        if not payment:
            continue
        attempt.status = "abandoned"
        attempt.abandoned_at = now
        attempt.response_description = "Submission worker stopped before acceptance was recorded"
        payment.provider_acceptance_state = "uncertain"
        payment.review_status = "needs_review"
        payment.review_reason = "stale_stk_submission_abandoned"
        if payment.status == "pending":
            transition_and_record(
                db,
                payment=payment,
                target="unknown",
                event_type="payment.unknown",
                details={"attempt_id": attempt.id, "reason": payment.review_reason},
            )
            queue_webhooks(db, payment, "payment.unknown")
        audit(
            db,
            organization_id=payment.organization_id,
            merchant_id=payment.merchant_account_id,
            action="stk_submission_abandoned",
            entity_type="payment_attempt",
            entity_id=attempt.id,
            metadata={
                "payment_id": payment.id,
                "submission_started_at": attempt.submission_started_at.isoformat(),
                "timeout_seconds": settings.STK_SUBMITTING_TIMEOUT_SECONDS,
            },
        )
        recovered.append(attempt.id)
    db.commit()
    return recovered


def retention_candidates(db: Session) -> dict[str, int]:
    """Report archive candidates without deleting payment evidence.

    Raw callbacks and status evidence require an approved archive/export flow;
    enabling deletion alone is intentionally insufficient to remove rows.
    """

    now = utcnow()
    return {
        "callbacks": db.query(MpesaCallback)
        .filter(MpesaCallback.received_at < now - timedelta(days=settings.CALLBACK_RETENTION_DAYS))
        .count(),
        "webhook_deliveries": db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.created_at
            < now - timedelta(days=settings.WEBHOOK_DELIVERY_RETENTION_DAYS),
            WebhookDelivery.status.in_(["delivered", "dead_letter"]),
        )
        .count(),
        "status_checks": db.query(PaymentStatusCheck)
        .filter(
            PaymentStatusCheck.checked_at
            < now - timedelta(days=settings.STATUS_CHECK_RETENTION_DAYS)
        )
        .count(),
    }
