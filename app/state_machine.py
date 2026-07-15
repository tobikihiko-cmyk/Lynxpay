"""Explicit, conservative LynxPay payment state transitions."""

from __future__ import annotations


class InvalidPaymentTransitionError(ValueError):
    pass


RETRYABLE_STATES = {"failed", "timeout", "unknown"}


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "created": {"pending"},
    # A request can fail before submission or become uncertain while Daraja is
    # accepting it. Neither case may remain as an ordinary pending payment.
    "pending": {"stk_sent", "failed", "unknown"},
    "stk_sent": {"success", "failed", "timeout", "unknown"},
    "unknown": {"success", "failed"},
    "success": {"reversed"},
    "failed": set(),
    "timeout": set(),
    "cancelled": set(),
    "reversed": set(),
}


def ensure_transition(current: str, target: str) -> None:
    if current == target:
        return
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise InvalidPaymentTransitionError(f"payment cannot transition from {current} to {target}")


def transition_payment(payment, target: str) -> str:
    previous = payment.status
    ensure_transition(previous, target)
    payment.status = target
    return previous


def begin_payment_retry(payment) -> str:
    """Start an explicit retry without making terminal regressions generally legal."""

    previous = payment.status
    if previous not in RETRYABLE_STATES:
        raise InvalidPaymentTransitionError(f"payment cannot be retried from {previous}")
    payment.status = "pending"
    return previous
