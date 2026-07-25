"""Shared invariants for the M-PESA reversal workflow."""

from __future__ import annotations

from decimal import Decimal

from app.models import Payment, ReversalRequest


def payment_reversal_binding(payment: Payment) -> dict[str, str]:
    return {
        "organization_id": payment.organization_id,
        "merchant_account_id": payment.merchant_account_id,
        "payment_id": payment.id,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "mpesa_receipt_number": payment.mpesa_receipt_number or "",
    }


def bind_reversal_to_payment(reversal: ReversalRequest, payment: Payment) -> None:
    evidence = dict(reversal.request_payload_redacted or {})
    evidence["binding"] = payment_reversal_binding(payment)
    reversal.request_payload_redacted = evidence


def reversal_binding_error(
    reversal: ReversalRequest,
    payment: Payment | None,
    *,
    eligible_statuses: set[str] | None = None,
) -> str | None:
    if payment is None:
        return "The original payment no longer exists"
    if payment.status not in (eligible_statuses or {"success"}):
        return f"The original payment is in terminal state {payment.status}"
    if payment.purpose != "payment":
        return "Merchant verification payments cannot be reversed"
    if not payment.mpesa_receipt_number:
        return "The original payment has no M-PESA receipt evidence"
    if Decimal(payment.amount) != Decimal(reversal.amount):
        return "The reversal amount no longer matches the original payment"
    if payment.currency != reversal.currency or payment.currency != "KES":
        return "The reversal currency no longer matches the original payment"
    if Decimal(reversal.amount) != Decimal(reversal.amount).to_integral_value():
        return "Daraja reversals require a whole KES amount"

    binding = (reversal.request_payload_redacted or {}).get("binding")
    if not isinstance(binding, dict):
        return "The reversal is missing its original payment binding"
    expected = payment_reversal_binding(payment)
    mismatches = [key for key, value in expected.items() if str(binding.get(key, "")) != str(value)]
    if mismatches:
        return f"The original payment binding changed ({', '.join(mismatches)})"
    return None


def merge_reversal_request_evidence(reversal: ReversalRequest, key: str, value: object) -> None:
    evidence = dict(reversal.request_payload_redacted or {})
    evidence[key] = value
    reversal.request_payload_redacted = evidence


def merge_reversal_response_evidence(reversal: ReversalRequest, key: str, value: object) -> None:
    evidence = dict(reversal.response_payload or {})
    evidence[key] = value
    reversal.response_payload = evidence
