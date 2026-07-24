"""Domain operations shared by LynxPay routes."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any
import uuid

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_client_ip
from app.core.security import decrypt_sensitive_values, is_encrypted_value
from app.daraja import DarajaSecrets
from app.deps import Principal
from app.models import (
    AuditLog,
    DarajaCredential,
    Invoice,
    MerchantAccount,
    MpesaCallback,
    Payment,
    PaymentLedgerEntry,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.schemas import normalize_kenyan_phone
from app.state_machine import begin_payment_retry, transition_payment


def utcnow() -> datetime:
    return datetime.now(timezone.utc)  # - deployed Python 3.10 compatibility


def request_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def audit(
    db: Session,
    *,
    organization_id: str,
    merchant_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str,
    principal: Principal | None = None,
    request: Request | None = None,
    metadata: dict | None = None,
) -> AuditLog:
    audit_metadata = dict(metadata or {})
    if request and (correlation_id := getattr(request.state, "request_id", None)):
        audit_metadata.setdefault("correlation_id", correlation_id)
    entry = AuditLog(
        organization_id=organization_id,
        merchant_account_id=merchant_id,
        actor_user_id=principal.user_id if principal else None,
        actor_api_key_id=principal.api_key_id if principal else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata_json=audit_metadata or None,
        ip_address=get_client_ip(request) if request else None,
        user_agent=request.headers.get("user-agent", "")[:500] if request else None,
    )
    db.add(entry)
    return entry


def ledger(
    db: Session,
    *,
    payment: Payment,
    event_type: str,
    status_from: str | None,
    details: dict | None = None,
) -> PaymentLedgerEntry:
    entry = PaymentLedgerEntry(
        organization_id=payment.organization_id,
        merchant_account_id=payment.merchant_account_id,
        payment_id=payment.id,
        event_type=event_type,
        status_from=status_from,
        status_to=payment.status,
        amount=payment.amount,
        currency=payment.currency,
        details=details,
    )
    db.add(entry)
    return entry


def transition_and_record(
    db: Session,
    *,
    payment: Payment,
    target: str,
    event_type: str,
    principal: Principal | None = None,
    request: Request | None = None,
    details: dict | None = None,
) -> None:
    previous = transition_payment(payment, target)
    ledger(db, payment=payment, event_type=event_type, status_from=previous, details=details)
    if target == "success" and payment.invoice_id:
        invoice = (
            db.query(Invoice)
            .filter(
                Invoice.id == payment.invoice_id,
                Invoice.organization_id == payment.organization_id,
                Invoice.merchant_account_id == payment.merchant_account_id,
            )
            .first()
        )
        if invoice and invoice.status != "paid":
            invoice.status = "paid"
            invoice.payment_id = payment.id
            invoice.paid_at = utcnow()
    elif target == "reversed" and payment.invoice_id:
        invoice = (
            db.query(Invoice)
            .filter(
                Invoice.id == payment.invoice_id,
                Invoice.organization_id == payment.organization_id,
                Invoice.merchant_account_id == payment.merchant_account_id,
                Invoice.payment_id == payment.id,
            )
            .first()
        )
        if invoice:
            invoice.status = "sent"
            invoice.payment_id = None
            invoice.paid_at = None
    audit(
        db,
        organization_id=payment.organization_id,
        merchant_id=payment.merchant_account_id,
        action=f"payment_marked_{target}"
        if target in {"success", "failed"}
        else event_type.replace(".", "_"),
        entity_type="payment",
        entity_id=payment.id,
        principal=principal,
        request=request,
        metadata={"from": previous, "to": target, **(details or {})},
    )


def begin_retry_and_record(
    db: Session,
    *,
    payment: Payment,
    principal: Principal,
    request: Request,
    attempt_number: int,
    reason: str,
) -> None:
    previous = begin_payment_retry(payment)
    details = {"attempt_number": attempt_number, "reason": reason}
    ledger(
        db,
        payment=payment,
        event_type="payment.retry_started",
        status_from=previous,
        details=details,
    )
    audit(
        db,
        organization_id=payment.organization_id,
        merchant_id=payment.merchant_account_id,
        action="payment_retry_initiated",
        entity_type="payment",
        entity_id=payment.id,
        principal=principal,
        request=request,
        metadata={"from": previous, "to": "pending", **details},
    )


def active_credential(db: Session, merchant: MerchantAccount) -> DarajaCredential:
    credential = (
        db.query(DarajaCredential)
        .filter(
            DarajaCredential.merchant_account_id == merchant.id,
            DarajaCredential.is_active.is_(True),
            DarajaCredential.environment == merchant.environment,
        )
        .first()
    )
    if not credential:
        raise HTTPException(status_code=409, detail="Active Daraja credentials are required")
    return credential


def decrypted_secrets(credential: DarajaCredential) -> DarajaSecrets:
    ciphertexts = (
        credential.consumer_key_encrypted,
        credential.consumer_secret_encrypted,
        credential.passkey_encrypted,
    )
    if not all(is_encrypted_value(value) for value in ciphertexts):
        raise HTTPException(status_code=500, detail="Daraja credential encryption invariant failed")
    values = decrypt_sensitive_values(list(ciphertexts))
    if not all(values):
        raise HTTPException(status_code=500, detail="Daraja credentials could not be decrypted")
    return DarajaSecrets(*values)


def decrypted_reversal_credentials(credential: DarajaCredential) -> tuple[str, str]:
    ciphertexts = (
        credential.initiator_name_encrypted,
        credential.security_credential_encrypted,
    )
    if not all(is_encrypted_value(value) for value in ciphertexts):
        raise HTTPException(
            status_code=409,
            detail="Daraja initiator name and security credential are required for reversals",
        )
    values = decrypt_sensitive_values(list(ciphertexts))
    if not all(values):
        raise HTTPException(
            status_code=500,
            detail="Daraja reversal credentials could not be decrypted",
        )
    return values[0], values[1]


def payment_payload(payment: Payment) -> dict:
    return {
        "id": payment.id,
        "merchant_id": payment.merchant_account_id,
        "external_reference": payment.external_reference,
        "order_id": payment.order_id,
        "invoice_id": payment.invoice_id,
        "customer_name": payment.customer_name,
        "customer_phone": payment.customer_phone,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "description": payment.description,
        "purpose": payment.purpose,
        "correlation_id": payment.correlation_id,
        "status": payment.status,
        "success_source": payment.success_source,
        "receipt_status": payment.receipt_status,
        "review_status": payment.review_status,
        "review_reason": payment.review_reason,
        "provider_acceptance_state": payment.provider_acceptance_state,
        "checkout_request_id": payment.checkout_request_id,
        "merchant_request_id": payment.merchant_request_id,
        "mpesa_receipt_number": payment.mpesa_receipt_number,
        "result_code": payment.result_code,
        "result_description": payment.result_description,
        "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
        "failed_at": payment.failed_at.isoformat() if payment.failed_at else None,
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
        "updated_at": payment.updated_at.isoformat() if payment.updated_at else None,
    }


def queue_webhooks(db: Session, payment: Payment, event_type: str) -> list[WebhookDelivery]:
    endpoints = (
        db.query(WebhookEndpoint)
        .filter(
            WebhookEndpoint.organization_id == payment.organization_id,
            WebhookEndpoint.status == "active",
        )
        .all()
    )
    payload = {
        "id": f"evt_{uuid.uuid4()}",
        "correlation_id": payment.correlation_id,
        "event": event_type,
        "created_at": utcnow().isoformat(),
        "data": {"payment": payment_payload(payment)},
    }
    queued = []
    for endpoint in endpoints:
        if (
            endpoint.merchant_account_id
            and endpoint.merchant_account_id != payment.merchant_account_id
        ):
            continue
        if event_type not in (endpoint.event_types or []):
            continue
        delivery = WebhookDelivery(
            webhook_endpoint_id=endpoint.id,
            payment_id=payment.id,
            event_type=event_type,
            payload=payload,
            status="queued",
            attempts=0,
            max_attempts=settings.WEBHOOK_MAX_ATTEMPTS,
            next_retry_at=utcnow(),
        )
        db.add(delivery)
        queued.append(delivery)
    return queued


def callback_fields(payload: dict) -> dict:
    stk = payload.get("Body", {}).get("stkCallback", {})
    metadata = {
        item.get("Name"): item.get("Value")
        for item in stk.get("CallbackMetadata", {}).get("Item", [])
        if isinstance(item, dict) and item.get("Name")
    }
    return {
        "checkout_request_id": stk.get("CheckoutRequestID"),
        "merchant_request_id": stk.get("MerchantRequestID"),
        "result_code": str(stk["ResultCode"]) if "ResultCode" in stk else None,
        "result_description": stk.get("ResultDesc"),
        "mpesa_receipt_number": metadata.get("MpesaReceiptNumber"),
        "amount": metadata.get("Amount"),
        "phone": metadata.get("PhoneNumber"),
        "transaction_date": metadata.get("TransactionDate"),
    }


def callback_matches_payment(payment: Payment, fields: dict) -> tuple[bool, str | None]:
    if not fields["mpesa_receipt_number"]:
        return False, "success callback missing MpesaReceiptNumber"
    if fields["amount"] is None:
        return False, "success callback missing Amount"
    try:
        amount = Decimal(str(fields["amount"])).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return False, "success callback contains invalid Amount"
    if amount != Decimal(payment.amount).quantize(Decimal("0.01")):
        return False, "callback amount does not match payment"
    if fields["phone"] is not None:
        try:
            phone = normalize_kenyan_phone(fields["phone"])
        except ValueError:
            return False, "success callback contains invalid PhoneNumber"
        if phone != payment.customer_phone:
            return False, "callback phone does not match payment"
    return True, None


def callback_view(callback: MpesaCallback, *, include_raw: bool = False) -> dict:
    view = {
        "id": callback.id,
        "merchant_id": callback.merchant_account_id,
        "payment_id": callback.payment_id,
        "checkout_request_id": callback.checkout_request_id,
        "merchant_request_id": callback.merchant_request_id,
        "mpesa_receipt_number": callback.mpesa_receipt_number,
        "result_code": callback.result_code,
        "result_description": callback.result_description,
        "callback_amount": str(callback.callback_amount) if callback.callback_amount else None,
        "callback_phone": callback.callback_phone,
        "processed": callback.processed,
        "processing_status": callback.processing_status,
        "processed_at": callback.processed_at.isoformat() if callback.processed_at else None,
        "duplicate_of_callback_id": callback.duplicate_of_callback_id,
        "linked_at": callback.linked_at.isoformat() if callback.linked_at else None,
        "linked_by_user_id": callback.linked_by_user_id,
        "link_reason": callback.link_reason,
        "received_at": callback.received_at.isoformat() if callback.received_at else None,
        "source_ip": callback.source_ip,
    }
    if include_raw:
        view["raw_payload"] = callback.raw_payload
    return view
