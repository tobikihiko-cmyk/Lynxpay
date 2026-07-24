"""Shared callback body limits and source verification."""

from __future__ import annotations

from contextlib import suppress

from fastapi import Request

from app.core.config import settings
from app.core.deps import get_client_ip, ip_in_cidrs
from app.core.security import verify_callback_signature


def callback_allowed(request: Request) -> bool:
    if settings.mpesa_callback_hmac:
        return verify_callback_signature(
            request.state.lynxpay_raw_callback,
            request.headers.get("X-Safaricom-Signature"),
        )
    allowlist = settings.MPESA_CALLBACK_IP_ALLOWLIST.strip()
    return not allowlist or ip_in_cidrs(
        get_client_ip(request), allowlist, _label="MPESA_CALLBACK_IP_ALLOWLIST"
    )


async def read_callback_body(request: Request) -> tuple[bytes, bool]:
    maximum = settings.MAX_CALLBACK_BODY_BYTES
    body = bytearray()
    oversized = False
    content_length = request.headers.get("content-length")
    if content_length:
        with suppress(ValueError):
            oversized = int(content_length) > maximum
    async for chunk in request.stream():
        remaining = maximum - len(body)
        if remaining > 0:
            body.extend(chunk[:remaining])
        if len(chunk) > remaining:
            oversized = True
    return bytes(body), oversized
