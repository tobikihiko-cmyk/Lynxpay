"""Encrypted email outbox and SMTP delivery worker."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from email.message import EmailMessage
import json
import smtplib
import ssl

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    decrypt_sensitive_value,
    encrypt_sensitive_value,
    encryption_key_version,
)
from app.models import EmailOutbox
from app.service import utcnow


def enqueue_email(
    db: Session,
    *,
    organization_id: str,
    user_id: str | None,
    to_email: str,
    template: str,
    payload: dict,
) -> EmailOutbox:
    encrypted = encrypt_sensitive_value(json.dumps(payload, separators=(",", ":")))
    record = EmailOutbox(
        organization_id=organization_id,
        user_id=user_id,
        to_email=to_email,
        template=template,
        payload_encrypted=encrypted,
        encryption_key_version=encryption_key_version(encrypted),
        status="queued",
        attempts=0,
        max_attempts=settings.EMAIL_MAX_ATTEMPTS,
        next_attempt_at=utcnow(),
    )
    db.add(record)
    return record


def claim_emails(db: Session, worker_id: str, limit: int = 20) -> list[str]:
    if settings.EMAIL_DELIVERY_MODE != "smtp":
        return []
    now = utcnow()
    query = (
        db.query(EmailOutbox)
        .filter(
            or_(
                and_(
                    EmailOutbox.status.in_(["queued", "retry_scheduled"]),
                    EmailOutbox.next_attempt_at <= now,
                ),
                and_(EmailOutbox.status == "sending", EmailOutbox.lease_expires_at <= now),
            )
        )
        .order_by(EmailOutbox.next_attempt_at.asc())
        .limit(min(max(limit, 1), 100))
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    rows = query.all()
    for row in rows:
        row.status = "sending"
        row.lease_owner = worker_id
        row.lease_expires_at = now + timedelta(seconds=60)
    db.commit()
    return [row.id for row in rows]


def _render(record: EmailOutbox) -> tuple[str, str]:
    decrypted = decrypt_sensitive_value(record.payload_encrypted)
    if not decrypted:
        raise RuntimeError("Email payload could not be decrypted")
    payload = json.loads(decrypted)
    if record.template == "password_reset":
        return (
            "Reset your LynxPay password",
            f"A password reset was requested for your LynxPay account.\n\nReset it here: {payload['url']}\n\nThis link expires shortly. If you did not request it, ignore this email.",
        )
    if record.template == "email_verification":
        return (
            "Verify your LynxPay email",
            f"Verify your email to continue LynxPay merchant activation.\n\nVerify email: {payload['url']}\n\nThis link expires at {payload['expires_at']}.",
        )
    if record.template == "team_invitation":
        return (
            "You have been invited to LynxPay",
            f"You were invited to join {payload['organization_name']} on LynxPay.\n\nAccept the invitation: {payload['url']}\n\nThis invitation expires at {payload['expires_at']}.",
        )
    raise RuntimeError("Unknown email template")


def _smtp_send(record: EmailOutbox) -> None:
    subject, body = _render(record)
    message = EmailMessage()
    message["From"] = settings.EMAIL_FROM
    message["To"] = record.to_email
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as client:
        if settings.SMTP_STARTTLS:
            client.starttls(context=ssl.create_default_context())
        if settings.SMTP_USERNAME:
            client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        client.send_message(message)


async def deliver_claimed_email(db: Session, email_id: str, worker_id: str) -> EmailOutbox | None:
    record = (
        db.query(EmailOutbox)
        .filter(
            EmailOutbox.id == email_id,
            EmailOutbox.status == "sending",
            EmailOutbox.lease_owner == worker_id,
        )
        .first()
    )
    if not record:
        return None
    record.attempts += 1
    try:
        await asyncio.to_thread(_smtp_send, record)
        record.status = "sent"
        record.sent_at = utcnow()
        record.last_error = None
        record.next_attempt_at = None
    except Exception as exc:
        record.last_error = exc.__class__.__name__
        if record.attempts >= record.max_attempts:
            record.status = "dead_letter"
            record.next_attempt_at = None
        else:
            record.status = "retry_scheduled"
            record.next_attempt_at = utcnow() + timedelta(minutes=2**record.attempts)
    record.lease_owner = None
    record.lease_expires_at = None
    db.commit()
    db.refresh(record)
    return record
