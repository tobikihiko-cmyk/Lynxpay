"""Reliable, signed webhook delivery with database leasing and SSRF controls."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decrypt_sensitive_value
from app.models import WebhookDelivery, WebhookDeliveryAttempt, WebhookEndpoint
from app.observability import WEBHOOK_DELIVERY_OUTCOMES
from app.service import audit, utcnow


class UnsafeWebhookUrlError(ValueError):
    pass


async def resolve_public_addresses(hostname: str, port: int) -> list[str]:
    """Resolve once and retain only globally routable addresses."""

    try:
        rows = await asyncio.get_running_loop().getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeWebhookUrlError("Webhook hostname could not be resolved") from exc
    addresses: list[str] = []
    for row in rows:
        address = row[4][0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if parsed.is_global and address not in addresses:
            addresses.append(address)
    if not addresses:
        raise UnsafeWebhookUrlError("Webhook hostname does not resolve to a public address")
    return addresses


def _validated_target(url: str) -> tuple[SplitResult, int]:
    target = urlsplit(url)
    allowed_schemes = {"https"} if settings.is_production else {"https", "http"}
    if target.scheme not in allowed_schemes or not target.hostname:
        raise UnsafeWebhookUrlError("Webhook URL must use an allowed HTTP scheme")
    if target.username or target.password or target.fragment:
        raise UnsafeWebhookUrlError("Webhook URL cannot include credentials or a fragment")
    try:
        port = target.port or (443 if target.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeWebhookUrlError("Webhook URL contains an invalid port") from exc
    if port not in {80, 443}:
        raise UnsafeWebhookUrlError("Webhook URL must use port 80 or 443")
    return target, port


def validate_webhook_url(url: str) -> None:
    _validated_target(url)


def canonical_payload(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def sign_payload(secret: str, timestamp: int, body: bytes) -> str:
    signed = str(timestamp).encode() + b"." + body
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


async def send_webhook(endpoint: WebhookEndpoint, delivery: WebhookDelivery) -> tuple[int, str]:
    """Send to a DNS-pinned public address while retaining Host and TLS SNI."""

    target, port = _validated_target(endpoint.url)
    addresses = await resolve_public_addresses(target.hostname or "", port)
    chosen = addresses[secrets.randbelow(len(addresses))]
    ip_host = f"[{chosen}]" if ":" in chosen else chosen
    netloc = f"{ip_host}:{port}"
    pinned_url = urlunsplit((target.scheme, netloc, target.path or "/", target.query, ""))
    host_header = target.hostname or ""
    if port != (443 if target.scheme == "https" else 80):
        host_header = f"{host_header}:{port}"

    secret = decrypt_sensitive_value(endpoint.secret_encrypted)
    if not secret:
        raise RuntimeError("Webhook signing secret could not be decrypted")
    body = canonical_payload(delivery.payload)
    timestamp = int(utcnow().timestamp())
    headers = {
        "Content-Type": "application/json",
        "Host": host_header,
        "User-Agent": "LynxPay-Webhooks/1.0",
        "X-LynxPay-Delivery-Id": delivery.id,
        "X-LynxPay-Event": delivery.event_type,
        "X-LynxPay-Signature": sign_payload(secret, timestamp, body),
    }
    timeout = httpx.Timeout(
        settings.WEBHOOK_TOTAL_TIMEOUT_SECONDS,
        connect=settings.WEBHOOK_CONNECT_TIMEOUT_SECONDS,
    )
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, trust_env=False
    ) as client:
        request = client.build_request("POST", pinned_url, headers=headers, content=body)
        request.extensions["sni_hostname"] = target.hostname
        response = await client.send(request, stream=True)
        captured = bytearray()
        try:
            async for chunk in response.aiter_bytes():
                remaining = settings.WEBHOOK_MAX_RESPONSE_BYTES - len(captured)
                if remaining <= 0:
                    break
                captured.extend(chunk[:remaining])
        finally:
            await response.aclose()
    return response.status_code, captured.decode("utf-8", errors="replace")


def claim_deliveries(db: Session, worker_id: str, limit: int = 20) -> list[str]:
    """Atomically lease due rows; PostgreSQL workers use SKIP LOCKED."""

    now = utcnow()
    due = and_(
        WebhookDelivery.status.in_(["queued", "retry_scheduled"]),
        or_(WebhookDelivery.next_retry_at.is_(None), WebhookDelivery.next_retry_at <= now),
    )
    expired = and_(
        WebhookDelivery.status == "delivering",
        WebhookDelivery.lease_expires_at <= now,
    )
    requested = min(max(limit, 1), 100)
    per_endpoint = max(settings.WEBHOOK_CLAIM_PER_ENDPOINT, 1)
    ranked = (
        db.query(
            WebhookDelivery.id.label("delivery_id"),
            func.row_number()
            .over(
                partition_by=WebhookDelivery.webhook_endpoint_id,
                order_by=(
                    WebhookDelivery.next_retry_at.asc(),
                    WebhookDelivery.created_at.asc(),
                ),
            )
            .label("endpoint_rank"),
        )
        .join(WebhookEndpoint, WebhookEndpoint.id == WebhookDelivery.webhook_endpoint_id)
        .filter(or_(due, expired), WebhookEndpoint.status == "active")
        .subquery()
    )
    query = (
        db.query(WebhookDelivery)
        .join(ranked, ranked.c.delivery_id == WebhookDelivery.id)
        .filter(ranked.c.endpoint_rank <= per_endpoint)
        .order_by(WebhookDelivery.next_retry_at.asc(), WebhookDelivery.created_at.asc())
        .limit(requested)
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True, of=WebhookDelivery)
    rows = query.all()
    lease_expires_at = now + timedelta(seconds=settings.WEBHOOK_LEASE_SECONDS)
    for delivery in rows:
        delivery.status = "delivering"
        delivery.lease_owner = worker_id
        delivery.lease_expires_at = lease_expires_at
    db.commit()
    return [delivery.id for delivery in rows]


def _retry_delay(attempt: int) -> int:
    base = settings.WEBHOOK_RETRY_BASE_SECONDS * (2 ** max(attempt - 1, 0))
    capped = min(base, 24 * 60 * 60)
    return capped + secrets.randbelow(max(settings.WEBHOOK_RETRY_BASE_SECONDS, 1))


async def deliver_claimed(db: Session, delivery_id: str, worker_id: str) -> WebhookDelivery | None:
    delivery = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.status == "delivering",
            WebhookDelivery.lease_owner == worker_id,
        )
        .first()
    )
    if not delivery:
        return None
    endpoint = (
        db.query(WebhookEndpoint).filter(WebhookEndpoint.id == delivery.webhook_endpoint_id).first()
    )
    now = utcnow()
    delivery.attempts += 1
    delivery.last_attempt_at = now
    attempt = WebhookDeliveryAttempt(
        webhook_delivery_id=delivery.id,
        attempt_number=delivery.attempts,
        status="started",
        started_at=now,
    )
    db.add(attempt)
    db.commit()  # Preserve proof that an outbound attempt began before network I/O.
    try:
        if not endpoint or endpoint.status != "active":
            raise RuntimeError("Webhook endpoint is not active")
        status_code, response_body = await send_webhook(endpoint, delivery)
        delivery.response_status_code = status_code
        delivery.response_body = response_body
        if 200 <= status_code < 300:
            delivery.status = "delivered"
            delivery.delivered_at = now
            delivery.next_retry_at = None
            delivery.last_error = None
            attempt.status = "delivered"
            endpoint.consecutive_failures = 0
            endpoint.pause_reason = None
            endpoint.paused_at = None
        else:
            delivery.last_error = f"HTTP {status_code}"
            attempt.status = "failed"
        attempt.response_status_code = status_code
        attempt.response_body = response_body
    except Exception as exc:  # the worker must persist every transport failure
        delivery.last_error = str(exc)[:1000] or exc.__class__.__name__
        attempt.status = "failed"
        attempt.error = delivery.last_error

    attempt.finished_at = utcnow()

    if attempt.status == "failed" and endpoint and endpoint.status == "active":
        endpoint.consecutive_failures = (endpoint.consecutive_failures or 0) + 1
        if endpoint.consecutive_failures >= settings.WEBHOOK_AUTO_PAUSE_FAILURES:
            endpoint.status = "paused"
            endpoint.paused_at = utcnow()
            endpoint.pause_reason = "consecutive_delivery_failures"
            delivery.status = "dead_letter"
            delivery.last_error = "Endpoint auto-paused after repeated delivery failures"
            audit(
                db,
                organization_id=endpoint.organization_id,
                merchant_id=endpoint.merchant_account_id,
                action="webhook_endpoint_auto_paused",
                entity_type="webhook_endpoint",
                entity_id=endpoint.id,
                metadata={"consecutive_failures": endpoint.consecutive_failures},
            )

    if delivery.status not in {"delivered", "dead_letter"}:
        if delivery.attempts >= delivery.max_attempts:
            delivery.status = "dead_letter"
            delivery.next_retry_at = None
        else:
            delivery.status = "retry_scheduled"
            delivery.next_retry_at = now + timedelta(seconds=_retry_delay(delivery.attempts))
    delivery.lease_owner = None
    delivery.lease_expires_at = None
    WEBHOOK_DELIVERY_OUTCOMES.labels(delivery.status).inc()
    db.commit()
    db.refresh(delivery)
    return delivery
