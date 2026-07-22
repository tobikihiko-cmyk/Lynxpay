"""Conservative M-PESA STK result-code classification."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderOutcome:
    target: str
    category: str
    needs_review: bool = False


KNOWN_RESULTS: dict[str, ProviderOutcome] = {
    "0": ProviderOutcome("success", "completed"),
    "1": ProviderOutcome("failed", "insufficient_funds"),
    "1001": ProviderOutcome("failed", "subscriber_locked"),
    "1019": ProviderOutcome("timeout", "transaction_expired"),
    "1032": ProviderOutcome("failed", "customer_cancelled"),
    "1037": ProviderOutcome("timeout", "provider_callback_timeout"),
    "2001": ProviderOutcome("failed", "invalid_customer_credentials"),
    # Safaricom uses these generic codes for provider-side or unclassified
    # failures. They are not sufficient evidence for a terminal merchant state.
    "1025": ProviderOutcome("unknown", "provider_unclassified", True),
    "9999": ProviderOutcome("unknown", "provider_internal_error", True),
}


def classify_mpesa_result(code: str | int | None) -> ProviderOutcome:
    normalized = str(code) if code is not None else ""
    return KNOWN_RESULTS.get(
        normalized,
        ProviderOutcome("unknown", "unrecognized_provider_result", True),
    )
