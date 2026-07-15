"""API-key and response-secret controls for LynxPay."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.core.config import settings


def generate_api_key(environment: str) -> tuple[str, str, str]:
    prefix = f"slp_{'live' if environment == 'production' else 'test'}_{secrets.token_hex(6)}"
    full_key = f"{prefix}_{secrets.token_urlsafe(32)}"
    return full_key, prefix, hash_api_key(full_key)


def hash_api_key(value: str) -> str:
    """Keyed, deterministic digest; raw keys are never persisted."""
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def verify_api_key(value: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(value), expected_hash)


def api_key_prefix(value: str) -> str | None:
    parts = value.split("_")
    if len(parts) < 4 or parts[0] != "slp" or parts[1] not in {"live", "test"}:
        return None
    return "_".join(parts[:3])


def masked_secret(configured: bool) -> str | None:
    return "********" if configured else None
