"""LynxPay Phase 1 HTTP API."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import secrets
import time
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_client_ip, get_db, ip_in_cidrs
from app.core.security import (
    decrypt_sensitive_values,
    encrypt_sensitive_value,
    encrypt_sensitive_values,
    encryption_key_version,
    hash_opaque_token,
    verify_callback_signature,
)
from app.daraja import (
    DarajaClient,
    DarajaRequestNotSentError,
    redact_stk_payload,
)
from app.database import set_tenant_context
from app.deps import (
    Principal,
    require_control_admin,
    require_scope,
    scoped_merchant,
)
from app.models import (
    ApiKey,
    AuditLog,
    CatalogItem,
    DarajaCredential,
    Invoice,
    InvoiceLineItem,
    MerchantAccount,
    MpesaCallback,
    Organization,
    Payment,
    PaymentAttempt,
    PaymentLedgerEntry,
    PaymentStatusCheck,
    User,
    WebhookDelivery,
    WebhookDeliveryAttempt,
    WebhookEndpoint,
)
from app.observability import (
    CALLBACKS_DUPLICATE,
    CALLBACKS_PROCESSED,
    CALLBACKS_RECEIVED,
    DARAJA_REQUEST_DURATION,
    PAYMENT_OUTCOMES,
    PAYMENTS_CREATED,
    STK_PUSH_FAILED,
    STK_PUSH_SENT,
)
from app.provider_codes import classify_mpesa_result
from app.reconciliation import reconcile_payment
from app.schemas import (
    ApiKeyCreate,
    CatalogItemCreate,
    CatalogItemPatch,
    ConsentAcceptance,
    DarajaCredentialPatch,
    DarajaCredentialWrite,
    InvoiceCreate,
    InvoicePayRequest,
    MerchantCreate,
    MerchantUpdate,
    OrganizationUpdate,
    PaymentRetryRequest,
    StkPushCreate,
    WebhookEndpointCreate,
    WebhookEndpointUpdate,
    normalize_kenyan_phone,
)
from app.security import generate_api_key, masked_secret
from app.service import (
    active_credential,
    audit,
    begin_retry_and_record,
    callback_fields,
    callback_matches_payment,
    callback_view,
    decrypted_secrets,
    ledger,
    payment_payload,
    queue_webhooks,
    request_fingerprint,
    transition_and_record,
    utcnow,
)
from app.state_machine import InvalidPaymentTransitionError
from app.webhooks import UnsafeWebhookUrlError, validate_webhook_url

router = APIRouter(tags=["LynxPay"])


def _organization_view(organization: Organization) -> dict:
    return {
        "id": organization.id,
        "name": organization.name,
        "legal_name": organization.legal_name,
        "business_type": organization.business_type,
        "county": organization.county,
        "town": organization.town,
        "contact_email": organization.contact_email,
        "contact_phone": organization.contact_phone,
        "support_email": organization.support_email,
        "status": organization.status,
        "terms_accepted_at": organization.terms_accepted_at.isoformat()
        if organization.terms_accepted_at
        else None,
        "privacy_accepted_at": organization.privacy_accepted_at.isoformat()
        if organization.privacy_accepted_at
        else None,
        "accepted_terms_version": organization.accepted_terms_version,
        "accepted_privacy_version": organization.accepted_privacy_version,
        "current_terms_version": settings.TERMS_VERSION,
        "current_privacy_version": settings.PRIVACY_VERSION,
        "created_at": organization.created_at.isoformat() if organization.created_at else None,
        "updated_at": organization.updated_at.isoformat() if organization.updated_at else None,
    }


def _merchant_view(merchant: MerchantAccount) -> dict:
    return {
        "id": merchant.id,
        "organization_id": merchant.organization_id,
        "merchant_name": merchant.merchant_name,
        "shortcode": merchant.shortcode,
        "till_number": merchant.till_number,
        "shortcode_type": merchant.shortcode_type,
        "environment": merchant.environment,
        "status": merchant.status,
        "callback_url": merchant.callback_url,
        "approval_submitted_at": merchant.approval_submitted_at.isoformat()
        if merchant.approval_submitted_at
        else None,
        "approved_at": merchant.approved_at.isoformat() if merchant.approved_at else None,
        "rejected_at": merchant.rejected_at.isoformat() if merchant.rejected_at else None,
        "rejection_reason": merchant.rejection_reason,
        "suspended_at": merchant.suspended_at.isoformat() if merchant.suspended_at else None,
        "created_at": merchant.created_at.isoformat() if merchant.created_at else None,
        "updated_at": merchant.updated_at.isoformat() if merchant.updated_at else None,
    }


def _credential_view(credential: DarajaCredential) -> dict:
    return {
        "id": credential.id,
        "merchant_id": credential.merchant_account_id,
        "consumer_key": masked_secret(bool(credential.consumer_key_encrypted)),
        "consumer_secret": masked_secret(bool(credential.consumer_secret_encrypted)),
        "passkey": masked_secret(bool(credential.passkey_encrypted)),
        "initiator_name": masked_secret(bool(credential.initiator_name_encrypted)),
        "security_credential": masked_secret(bool(credential.security_credential_encrypted)),
        "shortcode": credential.shortcode,
        "environment": credential.environment,
        "is_active": credential.is_active,
        "last_tested_at": credential.last_tested_at.isoformat()
        if credential.last_tested_at
        else None,
        "created_at": credential.created_at.isoformat() if credential.created_at else None,
    }


def _ensure_https(url: str, environment: str) -> None:
    if environment == "production" and not url.lower().startswith("https://"):
        raise HTTPException(
            status_code=422, detail="Production callback and webhook URLs must use HTTPS"
        )


def _attempt_view(attempt: PaymentAttempt) -> dict:
    return {
        "id": attempt.id,
        "payment_id": attempt.payment_id,
        "attempt_number": attempt.attempt_number,
        "attempt_type": attempt.attempt_type,
        "status": attempt.status,
        "merchant_request_id": attempt.merchant_request_id,
        "checkout_request_id": attempt.checkout_request_id,
        "response_code": attempt.response_code,
        "response_description": attempt.response_description,
        "submission_started_at": attempt.submission_started_at.isoformat()
        if attempt.submission_started_at
        else None,
        "provider_responded_at": attempt.provider_responded_at.isoformat()
        if attempt.provider_responded_at
        else None,
        "abandoned_at": attempt.abandoned_at.isoformat() if attempt.abandoned_at else None,
        "created_at": attempt.created_at.isoformat() if attempt.created_at else None,
        "updated_at": attempt.updated_at.isoformat() if attempt.updated_at else None,
    }


def _payment_attempt_result(payment: Payment, attempt: PaymentAttempt) -> dict:
    result = payment_payload(payment)
    result["idempotent_replay"] = False
    result["attempt"] = _attempt_view(attempt)
    return result


def _invoice_payment_link(public_id: str) -> str:
    return f"/pay/{public_id}"


def _merchant_display_address(organization: Organization) -> str | None:
    parts = [value for value in (organization.town, organization.county) if value]
    return ", ".join(parts) if parts else None


def _invoice_view(invoice: Invoice) -> dict:
    return {
        "id": invoice.id,
        "merchant_id": invoice.merchant_account_id,
        "invoice_number": invoice.invoice_number,
        "public_id": invoice.public_id,
        "payment_link": _invoice_payment_link(invoice.public_id),
        "client_name": invoice.client_name,
        "client_phone": invoice.client_phone,
        "client_email": invoice.client_email,
        "service_title": invoice.service_title,
        "description": invoice.description,
        "amount": str(invoice.amount),
        "currency": invoice.currency,
        "status": invoice.status,
        "due_at": invoice.due_at.isoformat() if invoice.due_at else None,
        "sent_at": invoice.sent_at.isoformat() if invoice.sent_at else None,
        "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
        "voided_at": invoice.voided_at.isoformat() if invoice.voided_at else None,
        "payment_id": invoice.payment_id,
        "merchant_display_name": invoice.merchant_display_name,
        "merchant_display_address": invoice.merchant_display_address,
        "merchant_display_email": invoice.merchant_display_email,
        "merchant_display_phone": invoice.merchant_display_phone,
        "memo": invoice.memo,
        "line_items": [_invoice_line_item_view(item) for item in invoice.line_items],
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
        "updated_at": invoice.updated_at.isoformat() if invoice.updated_at else None,
    }


def _public_invoice_view(invoice: Invoice) -> dict:
    return {
        "public_id": invoice.public_id,
        "invoice_number": invoice.invoice_number,
        "client_name": invoice.client_name,
        "service_title": invoice.service_title,
        "description": invoice.description,
        "amount": str(invoice.amount),
        "currency": invoice.currency,
        "status": invoice.status,
        "due_at": invoice.due_at.isoformat() if invoice.due_at else None,
        "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
        "line_items": [_invoice_line_item_view(item) for item in invoice.line_items],
        "merchant": {
            "name": invoice.merchant_display_name,
            "address": invoice.merchant_display_address,
            "email": invoice.merchant_display_email,
            "phone": invoice.merchant_display_phone,
            "shortcode_type": invoice.merchant.shortcode_type if invoice.merchant else None,
            "shortcode": invoice.merchant.shortcode if invoice.merchant else None,
            "till_number": invoice.merchant.till_number if invoice.merchant else None,
        },
    }


def _catalog_item_view(item: CatalogItem) -> dict:
    return {
        "id": item.id,
        "merchant_id": item.merchant_account_id,
        "item_type": item.item_type,
        "name": item.name,
        "description": item.description,
        "unit_price": str(item.unit_price),
        "currency": item.currency,
        "sku": item.sku,
        "status": item.status,
        "sort_order": item.sort_order,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _invoice_line_item_view(item: InvoiceLineItem) -> dict:
    return {
        "id": item.id,
        "catalog_item_id": item.catalog_item_id,
        "position": item.position,
        "item_type": item.item_type,
        "name": item.name,
        "description": item.description,
        "quantity": str(item.quantity),
        "unit_price": str(item.unit_price),
        "line_total": str(item.line_total),
    }


def _catalog_query(db: Session, principal: Principal):
    query = db.query(CatalogItem).filter(CatalogItem.organization_id == principal.organization_id)
    if principal.merchant_id:
        query = query.filter(CatalogItem.merchant_account_id == principal.merchant_id)
    elif principal.api_key_id and principal.environment:
        query = query.join(
            MerchantAccount, MerchantAccount.id == CatalogItem.merchant_account_id
        ).filter(MerchantAccount.environment == principal.environment)
    return query


def _active_catalog_count(db: Session, merchant_id: str) -> int:
    return (
        db.query(CatalogItem)
        .filter(CatalogItem.merchant_account_id == merchant_id, CatalogItem.status == "active")
        .count()
    )


@router.get("/organization")
def get_organization(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    organization = db.query(Organization).filter(Organization.id == principal.organization_id).one()
    return _organization_view(organization)


@router.patch("/organization")
def update_organization(
    payload: OrganizationUpdate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    organization = db.query(Organization).filter(Organization.id == principal.organization_id).one()
    changes = payload.model_dump(exclude_unset=True, mode="json")
    before = {field: getattr(organization, field) for field in changes}
    for field, value in changes.items():
        setattr(organization, field, value)
    audit(
        db,
        organization_id=organization.id,
        merchant_id=None,
        action="business_profile_updated",
        entity_type="organization",
        entity_id=organization.id,
        principal=principal,
        request=request,
        metadata={"before": before, "changed_fields": list(changes)},
    )
    db.commit()
    db.refresh(organization)
    return _organization_view(organization)


@router.post("/organization/consents")
def accept_organization_consents(
    payload: ConsentAcceptance,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    if payload.terms_version != settings.TERMS_VERSION:
        raise HTTPException(status_code=409, detail="The accepted terms version is not current")
    if payload.privacy_version != settings.PRIVACY_VERSION:
        raise HTTPException(status_code=409, detail="The accepted privacy version is not current")
    organization = db.query(Organization).filter(Organization.id == principal.organization_id).one()
    now = utcnow()
    organization.terms_accepted_at = now
    organization.privacy_accepted_at = now
    organization.accepted_terms_version = payload.terms_version
    organization.accepted_privacy_version = payload.privacy_version
    audit(
        db,
        organization_id=organization.id,
        merchant_id=None,
        action="legal_terms_accepted",
        entity_type="organization",
        entity_id=organization.id,
        principal=principal,
        request=request,
        metadata={
            "terms_version": payload.terms_version,
            "privacy_version": payload.privacy_version,
        },
    )
    db.commit()
    return _organization_view(organization)


@router.post("/merchants", status_code=201)
def create_merchant(
    payload: MerchantCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    organization_id = principal.organization_id
    if not organization_id:
        raise HTTPException(status_code=403, detail="Organization membership is required")

    merchant_id = str(uuid.uuid4())
    if settings.PUBLIC_BASE_URL:
        callback_url = (
            f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/v1/callbacks/mpesa/{merchant_id}"
        )
    elif payload.callback_url:
        callback_url = str(payload.callback_url)
    else:
        raise HTTPException(
            status_code=422,
            detail="callback_url is required when PUBLIC_BASE_URL is not configured",
        )
    _ensure_https(callback_url, payload.environment)

    merchant = MerchantAccount(
        id=merchant_id,
        organization_id=organization_id,
        merchant_name=payload.merchant_name,
        shortcode=payload.shortcode,
        till_number=payload.till_number,
        shortcode_type=payload.shortcode_type,
        environment=payload.environment,
        status="pending_setup",
        callback_url=callback_url,
    )
    db.add(merchant)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="This organization already has that shortcode and environment"
        ) from None
    audit(
        db,
        organization_id=organization_id,
        merchant_id=merchant.id,
        action="merchant_created",
        entity_type="merchant_account",
        entity_id=merchant.id,
        principal=principal,
        request=request,
        metadata={"environment": merchant.environment, "shortcode_type": merchant.shortcode_type},
    )
    db.commit()
    db.refresh(merchant)
    return _merchant_view(merchant)


@router.get("/merchants")
def list_merchants(
    before: datetime | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("merchants:read")),
):
    query = db.query(MerchantAccount).filter(
        MerchantAccount.organization_id == principal.organization_id
    )
    if principal.merchant_id:
        query = query.filter(MerchantAccount.id == principal.merchant_id)
    if principal.api_key_id and principal.environment:
        query = query.filter(MerchantAccount.environment == principal.environment)
    if before:
        query = query.filter(MerchantAccount.created_at < before)
    page_size = min(max(limit, 1), 500)
    rows = query.order_by(MerchantAccount.created_at.desc()).limit(page_size).all()
    return {
        "items": [_merchant_view(item) for item in rows],
        "next_before": rows[-1].created_at.isoformat() if len(rows) == page_size else None,
    }


@router.get("/merchants/{merchant_id}")
def get_merchant(
    merchant_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("merchants:read")),
):
    return _merchant_view(scoped_merchant(db, principal, merchant_id))


@router.patch("/merchants/{merchant_id}")
def update_merchant(
    merchant_id: str,
    payload: MerchantUpdate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    if not principal.organization_id:
        raise HTTPException(status_code=403, detail="Organization membership is required")
    merchant = scoped_merchant(db, principal, merchant_id)
    changes = payload.model_dump(exclude_unset=True)
    if "callback_url" in changes:
        if settings.PUBLIC_BASE_URL:
            raise HTTPException(
                status_code=409,
                detail="Callback URL is managed by PUBLIC_BASE_URL and cannot be overridden",
            )
        changes["callback_url"] = str(changes["callback_url"])
        _ensure_https(changes["callback_url"], merchant.environment)
    requested_status = changes.get("status")
    if requested_status in {
        "credentials_added",
        "verified",
        "pending_approval",
        "rejected",
        "suspended",
    }:
        raise HTTPException(
            status_code=422, detail="Merchant verification status is system-managed"
        )
    if requested_status == "active":
        if merchant.environment == "production":
            raise HTTPException(
                status_code=409,
                detail="Production merchants require independent LynxPay platform approval",
            )
        credential = active_credential(db, merchant)
        if merchant.status != "verified" or not credential.last_tested_at:
            raise HTTPException(
                status_code=409,
                detail="Merchant credentials must be successfully verified before activation",
            )
        successful_test = (
            db.query(Payment)
            .filter(
                Payment.merchant_account_id == merchant.id,
                Payment.purpose == "merchant_verification",
                Payment.status == "success",
                Payment.created_at >= credential.last_tested_at,
            )
            .order_by(Payment.created_at.desc())
            .first()
        )
        if not successful_test:
            raise HTTPException(
                status_code=409,
                detail="A successful KES 1 merchant verification payment is required before activation",
            )
    before = {key: getattr(merchant, key) for key in changes}
    for field, value in changes.items():
        setattr(merchant, field, value)
    audit(
        db,
        organization_id=merchant.organization_id,
        merchant_id=merchant.id,
        action="merchant_updated",
        entity_type="merchant_account",
        entity_id=merchant.id,
        principal=principal,
        request=request,
        metadata={"before": before, "changed_fields": list(changes)},
    )
    db.commit()
    db.refresh(merchant)
    return _merchant_view(merchant)


@router.post("/merchants/{merchant_id}/submit-for-approval")
def submit_merchant_for_approval(
    merchant_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    merchant = scoped_merchant(db, principal, merchant_id)
    if merchant.environment != "production":
        raise HTTPException(status_code=409, detail="Only production merchants require approval")
    if merchant.status != "verified":
        raise HTTPException(status_code=409, detail="Merchant credentials must be verified first")
    user = db.query(User).filter(User.id == principal.user_id).first()
    if not user or not user.email_verified_at:
        raise HTTPException(status_code=409, detail="Owner email verification is required")
    organization = db.query(Organization).filter(Organization.id == merchant.organization_id).one()
    if (
        organization.accepted_terms_version != settings.TERMS_VERSION
        or organization.accepted_privacy_version != settings.PRIVACY_VERSION
        or not organization.terms_accepted_at
        or not organization.privacy_accepted_at
    ):
        raise HTTPException(
            status_code=409, detail="Current terms and privacy acceptance is required"
        )
    credential = active_credential(db, merchant)
    if not credential.last_tested_at:
        raise HTTPException(status_code=409, detail="Daraja credential test is required")
    successful_test = (
        db.query(Payment)
        .filter(
            Payment.merchant_account_id == merchant.id,
            Payment.purpose == "merchant_verification",
            Payment.status == "success",
            Payment.created_at >= credential.last_tested_at,
        )
        .order_by(Payment.created_at.desc())
        .first()
    )
    if not successful_test:
        raise HTTPException(status_code=409, detail="Successful KES 1 verification is required")
    merchant.status = "pending_approval"
    merchant.approval_submitted_at = utcnow()
    merchant.rejected_at = None
    merchant.rejection_reason = None
    audit(
        db,
        organization_id=merchant.organization_id,
        merchant_id=merchant.id,
        action="merchant_submitted_for_approval",
        entity_type="merchant_account",
        entity_id=merchant.id,
        principal=principal,
        request=request,
        metadata={"verification_payment_id": successful_test.id},
    )
    db.commit()
    return _merchant_view(merchant)


def _write_credential(
    *,
    db: Session,
    merchant: MerchantAccount,
    values: dict,
    principal: Principal,
    request: Request,
    action: str,
) -> DarajaCredential:
    current = (
        db.query(DarajaCredential)
        .filter(
            DarajaCredential.merchant_account_id == merchant.id,
            DarajaCredential.is_active.is_(True),
        )
        .first()
    )
    if current:
        current.is_active = False
    encrypted_values = encrypt_sensitive_values(
        [
            values["consumer_key"],
            values["consumer_secret"],
            values["passkey"],
            values.get("initiator_name"),
            values.get("security_credential"),
        ]
    )
    encrypted_consumer_key = encrypted_values[0]
    credential = DarajaCredential(
        merchant_account_id=merchant.id,
        consumer_key_encrypted=encrypted_consumer_key,
        consumer_secret_encrypted=encrypted_values[1],
        passkey_encrypted=encrypted_values[2],
        shortcode=values["shortcode"],
        initiator_name_encrypted=encrypted_values[3],
        security_credential_encrypted=encrypted_values[4],
        environment=merchant.environment,
        encryption_key_version=encryption_key_version(encrypted_consumer_key),
        is_active=True,
    )
    db.add(credential)
    merchant.status = "credentials_added"
    db.flush()
    audit(
        db,
        organization_id=merchant.organization_id,
        merchant_id=merchant.id,
        action=action,
        entity_type="daraja_credential",
        entity_id=credential.id,
        principal=principal,
        request=request,
        metadata={"environment": merchant.environment, "shortcode": credential.shortcode},
    )
    db.commit()
    db.refresh(credential)
    return credential


@router.post("/merchants/{merchant_id}/daraja-credentials", status_code=201)
def create_daraja_credential(
    merchant_id: str,
    payload: DarajaCredentialWrite,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    merchant = scoped_merchant(db, principal, merchant_id)
    if payload.environment != merchant.environment:
        raise HTTPException(
            status_code=422, detail="Credential environment must match merchant environment"
        )
    if payload.shortcode != merchant.shortcode:
        raise HTTPException(
            status_code=422, detail="Credential shortcode must match merchant shortcode"
        )
    values = {
        key: (value.get_secret_value() if hasattr(value, "get_secret_value") else value)
        for key, value in payload.model_dump().items()
    }
    return _credential_view(
        _write_credential(
            db=db,
            merchant=merchant,
            values=values,
            principal=principal,
            request=request,
            action="credentials_added",
        )
    )


@router.patch("/merchants/{merchant_id}/daraja-credentials")
def update_daraja_credential(
    merchant_id: str,
    payload: DarajaCredentialPatch,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    merchant = scoped_merchant(db, principal, merchant_id)
    current = active_credential(db, merchant)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="At least one credential field is required")
    decrypted = decrypt_sensitive_values(
        [
            current.consumer_key_encrypted,
            current.consumer_secret_encrypted,
            current.passkey_encrypted,
            current.initiator_name_encrypted,
            current.security_credential_encrypted,
        ]
    )
    current_values = {
        "consumer_key": decrypted[0],
        "consumer_secret": decrypted[1],
        "passkey": decrypted[2],
        "shortcode": current.shortcode,
        "initiator_name": decrypted[3],
        "security_credential": decrypted[4],
    }
    for key, value in updates.items():
        current_values[key] = (
            value.get_secret_value() if hasattr(value, "get_secret_value") else value
        )
    if current_values["shortcode"] != merchant.shortcode:
        raise HTTPException(
            status_code=422, detail="Credential shortcode must match merchant shortcode"
        )
    return _credential_view(
        _write_credential(
            db=db,
            merchant=merchant,
            values=current_values,
            principal=principal,
            request=request,
            action="credentials_updated",
        )
    )


@router.post("/merchants/{merchant_id}/daraja-credentials/test")
async def test_daraja_credential(
    merchant_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    merchant = scoped_merchant(db, principal, merchant_id)
    credential = active_credential(db, merchant)
    try:
        await DarajaClient(merchant.environment).get_access_token(decrypted_secrets(credential))
    except Exception:
        audit(
            db,
            organization_id=merchant.organization_id,
            merchant_id=merchant.id,
            action="credentials_test_failed",
            entity_type="daraja_credential",
            entity_id=credential.id,
            principal=principal,
            request=request,
        )
        db.commit()
        raise HTTPException(status_code=502, detail="Daraja credential test failed") from None
    credential.last_tested_at = utcnow()
    merchant.status = "verified"
    audit(
        db,
        organization_id=merchant.organization_id,
        merchant_id=merchant.id,
        action="credentials_tested",
        entity_type="daraja_credential",
        entity_id=credential.id,
        principal=principal,
        request=request,
    )
    db.commit()
    return {
        "status": "valid",
        "merchant_status": merchant.status,
        "tested_at": credential.last_tested_at.isoformat(),
    }


@router.delete("/merchants/{merchant_id}/daraja-credentials", status_code=204)
def disable_daraja_credential(
    merchant_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    merchant = scoped_merchant(db, principal, merchant_id)
    credential = active_credential(db, merchant)
    credential.is_active = False
    merchant.status = "pending_setup"
    audit(
        db,
        organization_id=merchant.organization_id,
        merchant_id=merchant.id,
        action="credential_disabled",
        entity_type="daraja_credential",
        entity_id=credential.id,
        principal=principal,
        request=request,
    )
    db.commit()
    return None


@router.post("/api-keys", status_code=201)
def create_api_key(
    payload: ApiKeyCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    if not principal.organization_id:
        raise HTTPException(status_code=403, detail="Create a merchant before creating API keys")
    environment = payload.environment
    if payload.merchant_id:
        merchant = scoped_merchant(db, principal, payload.merchant_id)
        if merchant.environment != environment:
            raise HTTPException(
                status_code=422, detail="API key environment must match its merchant"
            )
        if environment == "production" and merchant.status != "active":
            raise HTTPException(
                status_code=409,
                detail="Production API keys can only be issued for an approved active merchant",
            )
    if (
        environment == "production"
        and "payments:write" in payload.scopes
        and not payload.merchant_id
    ):
        raise HTTPException(
            status_code=422,
            detail="Production payment-write API keys must be bound to one merchant",
        )
    full_key, prefix, digest = generate_api_key(environment)
    record = ApiKey(
        organization_id=principal.organization_id,
        merchant_account_id=payload.merchant_id,
        key_prefix=prefix,
        key_hash=digest,
        name=payload.name,
        environment=environment,
        scopes=payload.scopes,
        status="active",
        expires_at=payload.expires_at,
        created_by_user_id=principal.user_id,
    )
    db.add(record)
    db.flush()
    audit(
        db,
        organization_id=record.organization_id,
        merchant_id=record.merchant_account_id,
        action="api_key_created",
        entity_type="api_key",
        entity_id=record.id,
        principal=principal,
        request=request,
        metadata={
            "name": record.name,
            "scopes": record.scopes,
            "key_prefix": prefix,
            "environment": environment,
        },
    )
    db.commit()
    return {
        "id": record.id,
        "name": record.name,
        "merchant_id": record.merchant_account_id,
        "key_prefix": record.key_prefix,
        "environment": record.environment,
        "scopes": record.scopes,
        "api_key": full_key,
        "warning": "This API key is shown once and cannot be recovered.",
    }


@router.get("/api-keys")
def list_api_keys(
    merchant_id: str | None = None,
    environment: str | None = None,
    status: str | None = None,
    before: datetime | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    if not principal.organization_id:
        return {"items": []}
    query = db.query(ApiKey).filter(ApiKey.organization_id == principal.organization_id)
    if merchant_id:
        scoped_merchant(db, principal, merchant_id)
        query = query.filter(ApiKey.merchant_account_id == merchant_id)
    if environment:
        query = query.filter(ApiKey.environment == environment)
    if status:
        query = query.filter(ApiKey.status == status)
    if before:
        query = query.filter(ApiKey.created_at < before)
    page_size = min(max(limit, 1), 500)
    records = query.order_by(ApiKey.created_at.desc()).limit(page_size).all()
    return {
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "merchant_id": item.merchant_account_id,
                "key_prefix": item.key_prefix,
                "environment": item.environment,
                "scopes": item.scopes,
                "status": item.status,
                "last_used_at": item.last_used_at.isoformat() if item.last_used_at else None,
                "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "created_by_user_id": item.created_by_user_id,
            }
            for item in records
        ],
        "next_before": records[-1].created_at.isoformat() if len(records) == page_size else None,
    }


@router.delete("/api-keys/{api_key_id}", status_code=204)
def revoke_api_key(
    api_key_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    record = (
        db.query(ApiKey)
        .filter(ApiKey.id == api_key_id, ApiKey.organization_id == principal.organization_id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="API key not found")
    record.status = "revoked"
    record.revoked_at = utcnow()
    audit(
        db,
        organization_id=record.organization_id,
        merchant_id=record.merchant_account_id,
        action="api_key_revoked",
        entity_type="api_key",
        entity_id=record.id,
        principal=principal,
        request=request,
    )
    db.commit()
    return None


@router.post("/catalog-items", status_code=201)
def create_catalog_item(
    payload: CatalogItemCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:write")),
):
    merchant = scoped_merchant(db, principal, payload.merchant_id)
    if _active_catalog_count(db, merchant.id) >= 20:
        raise HTTPException(
            status_code=409,
            detail="Each merchant can keep up to 20 active services or products",
        )
    item = CatalogItem(
        organization_id=merchant.organization_id,
        merchant_account_id=merchant.id,
        item_type=payload.item_type,
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
        unit_price=payload.unit_price,
        currency="KES",
        sku=payload.sku.strip() if payload.sku else None,
        status="active",
        sort_order=payload.sort_order,
    )
    db.add(item)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="This merchant already has a catalog item with that name"
        ) from None
    audit(
        db,
        organization_id=item.organization_id,
        merchant_id=item.merchant_account_id,
        action="catalog_item_created",
        entity_type="catalog_item",
        entity_id=item.id,
        principal=principal,
        request=request,
        metadata={
            "name": item.name,
            "item_type": item.item_type,
            "unit_price": str(item.unit_price),
        },
    )
    db.commit()
    db.refresh(item)
    return _catalog_item_view(item)


@router.get("/catalog-items")
def list_catalog_items(
    merchant_id: str | None = None,
    status: Literal["active", "archived"] | None = "active",
    item_type: Literal["service", "product"] | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:read")),
):
    query = _catalog_query(db, principal)
    if merchant_id:
        scoped_merchant(db, principal, merchant_id)
        query = query.filter(CatalogItem.merchant_account_id == merchant_id)
    if status:
        query = query.filter(CatalogItem.status == status)
    if item_type:
        query = query.filter(CatalogItem.item_type == item_type)
    rows = query.order_by(CatalogItem.sort_order.asc(), CatalogItem.created_at.desc()).all()
    return {"items": [_catalog_item_view(item) for item in rows], "limit": 20}


@router.patch("/catalog-items/{item_id}")
def update_catalog_item(
    item_id: str,
    payload: CatalogItemPatch,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:write")),
):
    item = _catalog_query(db, principal).filter(CatalogItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    changes = payload.model_dump(exclude_unset=True)
    if (
        changes.get("status") == "active"
        and item.status != "active"
        and _active_catalog_count(db, item.merchant_account_id) >= 20
    ):
        raise HTTPException(
            status_code=409,
            detail="Each merchant can keep up to 20 active services or products",
        )
    before = {key: getattr(item, key) for key in changes}
    for field, value in changes.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(item, field, value)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="This merchant already has a catalog item with that name"
        ) from None
    audit(
        db,
        organization_id=item.organization_id,
        merchant_id=item.merchant_account_id,
        action="catalog_item_updated",
        entity_type="catalog_item",
        entity_id=item.id,
        principal=principal,
        request=request,
        metadata={"before": before, "changed_fields": list(changes)},
    )
    db.commit()
    db.refresh(item)
    return _catalog_item_view(item)


def _invoices_query(db: Session, principal: Principal):
    query = db.query(Invoice).filter(Invoice.organization_id == principal.organization_id)
    if principal.merchant_id:
        query = query.filter(Invoice.merchant_account_id == principal.merchant_id)
    elif principal.api_key_id and principal.environment:
        query = query.join(
            MerchantAccount, MerchantAccount.id == Invoice.merchant_account_id
        ).filter(MerchantAccount.environment == principal.environment)
    return query


def _invoice_line_data(
    db: Session, *, organization_id: str, merchant_id: str, payload: InvoiceCreate
) -> tuple[list[dict], Decimal]:
    rows: list[dict] = []
    total = Decimal("0.00")
    for position, line in enumerate(payload.line_items, start=1):
        catalog_item = None
        if line.catalog_item_id:
            catalog_item = (
                db.query(CatalogItem)
                .filter(
                    CatalogItem.id == line.catalog_item_id,
                    CatalogItem.organization_id == organization_id,
                    CatalogItem.merchant_account_id == merchant_id,
                    CatalogItem.status == "active",
                )
                .first()
            )
            if not catalog_item:
                raise HTTPException(status_code=422, detail="Catalog item is not active")
        item_type = line.item_type or (catalog_item.item_type if catalog_item else "custom")
        name = line.name or (catalog_item.name if catalog_item else None)
        description = (
            line.description
            if line.description is not None
            else (catalog_item.description if catalog_item else None)
        )
        unit_price = line.unit_price or (catalog_item.unit_price if catalog_item else None)
        if not name or unit_price is None:
            raise HTTPException(status_code=422, detail="Invoice line is incomplete")
        line_total = (line.quantity * unit_price).quantize(Decimal("0.01"))
        if line_total != line_total.to_integral_value():
            raise HTTPException(status_code=422, detail="Invoice line totals must be whole KES")
        rows.append(
            {
                "catalog_item_id": catalog_item.id if catalog_item else None,
                "position": position,
                "item_type": item_type,
                "name": name,
                "description": description,
                "quantity": line.quantity,
                "unit_price": unit_price,
                "line_total": line_total,
            }
        )
        total += line_total
    return rows, total.quantize(Decimal("0.01"))


@router.post("/invoices", status_code=201)
def create_invoice(
    payload: InvoiceCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:write")),
):
    merchant = scoped_merchant(db, principal, payload.merchant_id)
    if merchant.status != "active":
        raise HTTPException(status_code=409, detail="Merchant must be active before invoicing")
    organization = db.query(Organization).filter(Organization.id == merchant.organization_id).one()
    now = utcnow()
    invoice_number = payload.invoice_number or f"INV-{now:%Y%m%d}-{secrets.token_hex(3).upper()}"
    invoice_amount = payload.amount or Decimal("0.00")
    line_items_data: list[dict] = []
    if payload.line_items:
        line_items_data, invoice_amount = _invoice_line_data(
            db,
            organization_id=merchant.organization_id,
            merchant_id=merchant.id,
            payload=payload,
        )
        if not line_items_data:
            raise HTTPException(status_code=422, detail="Invoice requires at least one line item")
    invoice = Invoice(
        organization_id=merchant.organization_id,
        merchant_account_id=merchant.id,
        invoice_number=invoice_number,
        public_id=f"inv_{secrets.token_urlsafe(24)}",
        client_name=payload.client_name,
        client_phone=payload.client_phone,
        client_email=str(payload.client_email) if payload.client_email else None,
        service_title=payload.service_title,
        description=payload.description,
        amount=invoice_amount,
        currency="KES",
        status="sent",
        due_at=payload.due_at,
        sent_at=now,
        merchant_display_name=organization.legal_name
        or organization.name
        or merchant.merchant_name,
        merchant_display_address=_merchant_display_address(organization),
        merchant_display_email=organization.support_email or organization.contact_email,
        merchant_display_phone=organization.contact_phone,
        memo=payload.memo,
    )
    db.add(invoice)
    try:
        db.flush()
        if line_items_data:
            line_items = [
                InvoiceLineItem(invoice_id=invoice.id, **line_item) for line_item in line_items_data
            ]
            db.add_all(line_items)
        elif invoice.amount <= 0:
            raise HTTPException(status_code=422, detail="Invoice amount must be positive")
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Invoice number already exists") from None
    audit(
        db,
        organization_id=invoice.organization_id,
        merchant_id=invoice.merchant_account_id,
        action="invoice_created",
        entity_type="invoice",
        entity_id=invoice.id,
        principal=principal,
        request=request,
        metadata={"invoice_number": invoice.invoice_number, "amount": str(invoice.amount)},
    )
    db.commit()
    db.refresh(invoice)
    return _invoice_view(invoice)


@router.get("/invoices")
def list_invoices(
    merchant_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
    before: datetime | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:read")),
):
    query = _invoices_query(db, principal)
    if merchant_id:
        scoped_merchant(db, principal, merchant_id)
        query = query.filter(Invoice.merchant_account_id == merchant_id)
    if status:
        query = query.filter(Invoice.status == status)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Invoice.invoice_number.ilike(term),
                Invoice.client_name.ilike(term),
                Invoice.client_phone.ilike(term),
                Invoice.service_title.ilike(term),
            )
        )
    if before:
        query = query.filter(Invoice.created_at < before)
    page_size = min(max(limit, 1), 500)
    records = query.order_by(Invoice.created_at.desc()).limit(page_size).all()
    return {
        "items": [_invoice_view(item) for item in records],
        "next_before": records[-1].created_at.isoformat() if len(records) == page_size else None,
    }


@router.get("/invoices/{invoice_id}")
def get_invoice(
    invoice_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:read")),
):
    invoice = _invoices_query(db, principal).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return _invoice_view(invoice)


@router.post("/invoices/{invoice_id}/void")
def void_invoice(
    invoice_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:write")),
):
    invoice = _invoices_query(db, principal).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == "paid":
        raise HTTPException(status_code=409, detail="Paid invoices cannot be voided")
    invoice.status = "void"
    invoice.voided_at = utcnow()
    audit(
        db,
        organization_id=invoice.organization_id,
        merchant_id=invoice.merchant_account_id,
        action="invoice_voided",
        entity_type="invoice",
        entity_id=invoice.id,
        principal=principal,
        request=request,
    )
    db.commit()
    db.refresh(invoice)
    return _invoice_view(invoice)


@router.get("/public/invoices/{public_id}")
def get_public_invoice(public_id: str, db: Session = Depends(get_db)):
    invoice = (
        db.query(Invoice)
        .join(MerchantAccount, MerchantAccount.id == Invoice.merchant_account_id)
        .filter(Invoice.public_id == public_id)
        .first()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return _public_invoice_view(invoice)


@router.post("/public/invoices/{public_id}/pay", status_code=201)
async def pay_public_invoice(
    public_id: str,
    payload: InvoicePayRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    invoice = (
        db.query(Invoice)
        .join(MerchantAccount, MerchantAccount.id == Invoice.merchant_account_id)
        .filter(Invoice.public_id == public_id)
        .with_for_update()
        .first()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == "paid":
        return {"invoice": _public_invoice_view(invoice), "payment": None, "already_paid": True}
    if invoice.status in {"void", "expired"}:
        raise HTTPException(status_code=409, detail="This invoice is no longer payable")
    merchant = invoice.merchant
    if not merchant or merchant.status != "active":
        raise HTTPException(status_code=409, detail="Merchant is not accepting invoice payments")
    credential = active_credential(db, merchant)
    pending = (
        db.query(Payment)
        .filter(
            Payment.invoice_id == invoice.id,
            Payment.status.in_(["created", "pending", "stk_sent", "unknown"]),
        )
        .order_by(Payment.created_at.desc())
        .first()
    )
    if pending:
        raise HTTPException(
            status_code=409,
            detail="An M-PESA prompt is already pending for this invoice. Wait for it to expire before trying again.",
        )
    attempt_count = db.query(Payment).filter(Payment.invoice_id == invoice.id).count()
    external_reference = f"{invoice.invoice_number}-{attempt_count + 1}"
    payment = Payment(
        organization_id=invoice.organization_id,
        merchant_account_id=invoice.merchant_account_id,
        external_reference=external_reference,
        idempotency_request_hash=request_fingerprint(
            {
                "invoice_id": invoice.id,
                "phone_number": payload.phone_number,
                "attempt": attempt_count + 1,
            }
        ),
        invoice_id=invoice.id,
        customer_name=invoice.client_name,
        customer_phone=payload.phone_number,
        amount=invoice.amount,
        currency=invoice.currency,
        description=invoice.service_title,
        callback_metadata={
            "invoice_id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "service_title": invoice.service_title,
        },
        status="created",
    )
    db.add(payment)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Invoice payment already exists") from None
    ledger(db, payment=payment, event_type="payment.created", status_from=None)
    audit(
        db,
        organization_id=payment.organization_id,
        merchant_id=payment.merchant_account_id,
        action="invoice_payment_created",
        entity_type="invoice",
        entity_id=invoice.id,
        request=request,
        metadata={"payment_id": payment.id, "invoice_number": invoice.invoice_number},
    )
    transition_and_record(
        db,
        payment=payment,
        target="pending",
        event_type="payment.pending",
        request=request,
        details={"invoice_id": invoice.id},
    )
    attempt = PaymentAttempt(
        payment_id=payment.id,
        merchant_account_id=merchant.id,
        attempt_number=1,
        phone_number=payment.customer_phone,
        amount=payment.amount,
        request_payload_redacted={
            "phone_number": f"{payment.customer_phone[:6]}***{payment.customer_phone[-3:]}",
            "amount": str(payment.amount),
            "external_reference": payment.external_reference,
            "invoice_number": invoice.invoice_number,
        },
        status="submitting",
        submission_started_at=utcnow(),
        attempt_type="invoice",
    )
    db.add(attempt)
    db.commit()
    PAYMENTS_CREATED.inc()
    system_principal = Principal(organization_id=invoice.organization_id, scopes=frozenset())
    result = await _submit_stk_attempt(
        db=db,
        payment=payment,
        attempt=attempt,
        merchant=merchant,
        credential=credential,
        principal=system_principal,
        request=request,
    )
    return {"invoice": _public_invoice_view(invoice), "payment": result, "already_paid": False}


async def _submit_stk_attempt(
    *,
    db: Session,
    payment: Payment,
    attempt: PaymentAttempt,
    merchant: MerchantAccount,
    credential: DarajaCredential,
    principal: Principal,
    request: Request,
) -> dict:
    """Submit one already-persisted attempt and record its acceptance evidence."""

    client = DarajaClient(merchant.environment)
    started = time.perf_counter()
    try:
        response, sent_payload = await client.stk_push(
            secrets=decrypted_secrets(credential),
            shortcode=credential.shortcode,
            till_number=merchant.till_number,
            shortcode_type=merchant.shortcode_type,
            phone=payment.customer_phone,
            amount=payment.amount,
            external_reference=payment.external_reference,
            description=payment.description,
            callback_url=merchant.callback_url,
        )
    except DarajaRequestNotSentError:
        STK_PUSH_FAILED.labels("not_sent").inc()
        attempt.status = "not_sent"
        attempt.provider_responded_at = utcnow()
        attempt.response_description = "Daraja STK request was not submitted"
        payment.provider_acceptance_state = "rejected"
        payment.receipt_status = "not_applicable"
        payment.review_status = "none"
        payment.review_reason = None
        transition_and_record(
            db,
            payment=payment,
            target="failed",
            event_type="payment.failed",
            principal=principal,
            request=request,
            details={"attempt_id": attempt.id, "reason": "provider_request_not_sent"},
        )
        payment.result_description = attempt.response_description
        payment.failed_at = utcnow()
        queue_webhooks(db, payment, "payment.failed")
        audit(
            db,
            organization_id=payment.organization_id,
            merchant_id=merchant.id,
            action="stk_push_request_failed",
            entity_type="payment_attempt",
            entity_id=attempt.id,
            principal=principal,
            request=request,
        )
        db.commit()
        return _payment_attempt_result(payment, attempt)
    except Exception:
        STK_PUSH_FAILED.labels("uncertain").inc()
        attempt.status = "uncertain"
        attempt.provider_responded_at = utcnow()
        attempt.response_description = "Daraja acceptance could not be verified"
        payment.provider_acceptance_state = "uncertain"
        payment.review_status = "needs_review"
        payment.review_reason = "provider_acceptance_uncertain"
        transition_and_record(
            db,
            payment=payment,
            target="unknown",
            event_type="payment.unknown",
            principal=principal,
            request=request,
            details={"attempt_id": attempt.id, "reason": "provider_acceptance_uncertain"},
        )
        payment.result_description = attempt.response_description
        queue_webhooks(db, payment, "payment.unknown")
        db.commit()
        return _payment_attempt_result(payment, attempt)
    finally:
        DARAJA_REQUEST_DURATION.labels("stk_push", merchant.environment).observe(
            time.perf_counter() - started
        )

    attempt.request_payload_redacted = redact_stk_payload(sent_payload)
    attempt.response_payload = response
    attempt.provider_responded_at = utcnow()
    attempt.merchant_request_id = response.get("MerchantRequestID")
    attempt.checkout_request_id = response.get("CheckoutRequestID")
    attempt.response_code = str(response.get("ResponseCode", ""))
    attempt.response_description = response.get("ResponseDescription") or response.get(
        "CustomerMessage"
    )
    if attempt.response_code != "0":
        STK_PUSH_FAILED.labels("provider_rejected").inc()
        attempt.status = "rejected"
        payment.provider_acceptance_state = "rejected"
        payment.receipt_status = "not_applicable"
        payment.review_status = "none"
        payment.review_reason = None
        transition_and_record(
            db,
            payment=payment,
            target="failed",
            event_type="payment.failed",
            principal=principal,
            request=request,
            details={"attempt_id": attempt.id, "response_code": attempt.response_code},
        )
        payment.result_code = attempt.response_code
        payment.result_description = attempt.response_description
        payment.failed_at = utcnow()
        queue_webhooks(db, payment, "payment.failed")
        db.commit()
        return _payment_attempt_result(payment, attempt)
    if not attempt.checkout_request_id:
        STK_PUSH_FAILED.labels("missing_checkout_request_id").inc()
        attempt.status = "uncertain"
        payment.provider_acceptance_state = "uncertain"
        payment.review_status = "needs_review"
        payment.review_reason = "accepted_without_checkout_request_id"
        transition_and_record(
            db,
            payment=payment,
            target="unknown",
            event_type="payment.unknown",
            principal=principal,
            request=request,
            details={"attempt_id": attempt.id, "reason": "missing_checkout_request_id"},
        )
        payment.result_description = "Daraja accepted the request without a CheckoutRequestID"
        queue_webhooks(db, payment, "payment.unknown")
        db.commit()
        return _payment_attempt_result(payment, attempt)

    payment.checkout_request_id = attempt.checkout_request_id
    payment.merchant_request_id = attempt.merchant_request_id
    payment.provider_acceptance_state = "accepted"
    payment.receipt_status = "missing"
    payment.review_status = "none"
    payment.review_reason = None
    payment.next_reconciliation_at = utcnow() + timedelta(
        seconds=settings.RECONCILIATION_INITIAL_DELAY_SECONDS
    )
    attempt.status = "accepted"
    transition_and_record(
        db,
        payment=payment,
        target="stk_sent",
        event_type="payment.stk_sent",
        principal=principal,
        request=request,
        details={"attempt_id": attempt.id, "checkout_request_id": attempt.checkout_request_id},
    )
    audit(
        db,
        organization_id=payment.organization_id,
        merchant_id=merchant.id,
        action="stk_push_initiated",
        entity_type="payment_attempt",
        entity_id=attempt.id,
        principal=principal,
        request=request,
        metadata={
            "checkout_request_id": attempt.checkout_request_id,
            "attempt_number": attempt.attempt_number,
            "attempt_type": attempt.attempt_type,
        },
    )
    queue_webhooks(db, payment, "payment.stk_sent")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        locked = db.query(Payment).filter(Payment.id == payment.id).with_for_update().one()
        locked.provider_acceptance_state = "uncertain"
        locked.review_status = "needs_review"
        locked.review_reason = "checkout_request_id_conflict"
        if locked.status == "pending":
            transition_and_record(
                db,
                payment=locked,
                target="unknown",
                event_type="payment.unknown",
                principal=principal,
                request=request,
                details={"reason": "checkout_request_id_conflict"},
            )
        audit(
            db,
            organization_id=locked.organization_id,
            merchant_id=locked.merchant_account_id,
            action="checkout_request_id_conflict",
            entity_type="payment",
            entity_id=locked.id,
            principal=principal,
            request=request,
        )
        db.commit()
        raise HTTPException(
            status_code=409, detail="Daraja CheckoutRequestID has already been recorded"
        ) from None
    db.refresh(payment)
    STK_PUSH_SENT.inc()
    return _payment_attempt_result(payment, attempt)


@router.post("/payments/stk-push", status_code=201)
async def create_stk_push(
    payload: StkPushCreate,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=255),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:write")),
):
    merchant = scoped_merchant(db, principal, payload.merchant_id)
    is_merchant_verification = payload.purpose == "merchant_verification"
    if is_merchant_verification and not principal.is_control_plane_admin:
        raise HTTPException(
            status_code=403,
            detail="Merchant verification payments require administrator authentication",
        )
    required_status = "verified" if is_merchant_verification else "active"
    if merchant.status != required_status:
        raise HTTPException(
            status_code=409,
            detail=(
                "Merchant credentials must be verified before the KES 1 test payment"
                if is_merchant_verification
                else "Merchant must be active before initiating STK Push"
            ),
        )
    credential = active_credential(db, merchant)
    request_data = payload.model_dump(mode="json")
    fingerprint = request_fingerprint(request_data)
    idempotency_digest = (
        hash_opaque_token(f"idempotency:{idempotency_key}") if idempotency_key else None
    )
    duplicate_query = db.query(Payment).filter(
        Payment.merchant_account_id == merchant.id,
        Payment.external_reference == payload.external_reference,
    )
    existing = duplicate_query.first()
    if not existing and idempotency_key:
        existing = (
            db.query(Payment)
            .filter(
                Payment.merchant_account_id == merchant.id,
                Payment.idempotency_key.in_([idempotency_digest, idempotency_key]),
            )
            .first()
        )
    if existing:
        if existing.idempotency_request_hash == fingerprint:
            replay = payment_payload(existing)
            replay["idempotent_replay"] = True
            return replay
        raise HTTPException(
            status_code=409,
            detail="Payment reference or idempotency key was reused with different data",
        )

    payment = Payment(
        organization_id=merchant.organization_id,
        merchant_account_id=merchant.id,
        external_reference=payload.external_reference,
        idempotency_key=idempotency_digest,
        idempotency_request_hash=fingerprint,
        order_id=payload.order_id,
        invoice_id=payload.invoice_id,
        customer_name=payload.customer_name,
        customer_phone=payload.phone_number,
        amount=payload.amount,
        currency="KES",
        description=payload.description,
        purpose=payload.purpose,
        callback_metadata=payload.callback_metadata,
        status="created",
    )
    db.add(payment)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Duplicate payment reference or idempotency key"
        ) from None
    ledger(db, payment=payment, event_type="payment.created", status_from=None)
    audit(
        db,
        organization_id=payment.organization_id,
        merchant_id=merchant.id,
        action="payment_created",
        entity_type="payment",
        entity_id=payment.id,
        principal=principal,
        request=request,
        metadata={
            "external_reference": payment.external_reference,
            "amount": str(payment.amount),
            "purpose": payment.purpose,
        },
    )
    transition_and_record(
        db,
        payment=payment,
        target="pending",
        event_type="payment.pending",
        principal=principal,
        request=request,
    )
    attempt = PaymentAttempt(
        payment_id=payment.id,
        merchant_account_id=merchant.id,
        attempt_number=1,
        phone_number=payment.customer_phone,
        amount=payment.amount,
        request_payload_redacted={
            "phone_number": f"{payment.customer_phone[:6]}***{payment.customer_phone[-3:]}",
            "amount": str(payment.amount),
            "external_reference": payment.external_reference,
        },
        status="submitting",
        submission_started_at=utcnow(),
        attempt_type="initial",
        initiated_by_user_id=principal.user_id,
        initiated_by_api_key_id=principal.api_key_id,
    )
    db.add(attempt)
    db.commit()
    PAYMENTS_CREATED.inc()
    return await _submit_stk_attempt(
        db=db,
        payment=payment,
        attempt=attempt,
        merchant=merchant,
        credential=credential,
        principal=principal,
        request=request,
    )


def _payments_query(db: Session, principal: Principal):
    query = db.query(Payment).filter(Payment.organization_id == principal.organization_id)
    if principal.merchant_id:
        query = query.filter(Payment.merchant_account_id == principal.merchant_id)
    elif principal.api_key_id and principal.environment:
        query = query.join(
            MerchantAccount, MerchantAccount.id == Payment.merchant_account_id
        ).filter(MerchantAccount.environment == principal.environment)
    return query


@router.get("/payments")
def list_payments(
    merchant_id: str | None = None,
    status: str | None = None,
    purpose: Literal["payment", "merchant_verification"] | None = None,
    environment: str | None = None,
    review_status: str | None = None,
    search: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    before: datetime | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:read")),
):
    query = _payments_query(db, principal)
    if merchant_id:
        scoped_merchant(db, principal, merchant_id)
        query = query.filter(Payment.merchant_account_id == merchant_id)
    if status:
        query = query.filter(Payment.status == status)
    if purpose:
        query = query.filter(Payment.purpose == purpose)
    if environment:
        query = query.filter(Payment.merchant.has(environment=environment))
    if review_status:
        query = query.filter(Payment.review_status == review_status)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Payment.external_reference.ilike(term),
                Payment.customer_phone.ilike(term),
                Payment.mpesa_receipt_number.ilike(term),
                Payment.checkout_request_id.ilike(term),
            )
        )
    if created_from:
        query = query.filter(Payment.created_at >= created_from)
    if created_to:
        query = query.filter(Payment.created_at <= created_to)
    if before:
        query = query.filter(Payment.created_at < before)
    page_size = min(max(limit, 1), 500)
    records = query.order_by(Payment.created_at.desc()).limit(page_size).all()
    return {
        "items": [payment_payload(item) for item in records],
        "next_before": records[-1].created_at.isoformat() if len(records) == page_size else None,
    }


@router.get("/payments/{payment_id}")
def get_payment(
    payment_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:read")),
):
    payment = _payments_query(db, principal).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment_payload(payment)


@router.get("/payments/{payment_id}/attempts")
def list_payment_attempts(
    payment_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:read")),
):
    payment = _payments_query(db, principal).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    rows = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.payment_id == payment.id)
        .order_by(PaymentAttempt.attempt_number.asc())
        .all()
    )
    return {"items": [_attempt_view(row) for row in rows]}


@router.get("/payments/{payment_id}/timeline")
def get_payment_timeline(
    payment_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:read")),
):
    payment = _payments_query(db, principal).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    attempts = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.payment_id == payment.id)
        .order_by(PaymentAttempt.attempt_number.asc())
        .all()
    )
    callbacks = (
        db.query(MpesaCallback)
        .filter(MpesaCallback.payment_id == payment.id)
        .order_by(MpesaCallback.received_at.asc())
        .all()
    )
    ledger_rows = (
        db.query(PaymentLedgerEntry)
        .filter(PaymentLedgerEntry.payment_id == payment.id)
        .order_by(PaymentLedgerEntry.created_at.asc())
        .all()
    )
    status_checks = (
        db.query(PaymentStatusCheck)
        .filter(PaymentStatusCheck.payment_id == payment.id)
        .order_by(PaymentStatusCheck.checked_at.asc())
        .all()
    )
    return {
        "payment": payment_payload(payment),
        "attempts": [_attempt_view(row) for row in attempts],
        "callbacks": [callback_view(row) for row in callbacks],
        "ledger": [
            {
                "id": row.id,
                "event_type": row.event_type,
                "status_from": row.status_from,
                "status_to": row.status_to,
                "details": row.details,
                "created_at": row.created_at.isoformat(),
            }
            for row in ledger_rows
        ],
        "status_checks": [
            {
                "id": row.id,
                "outcome": row.outcome,
                "result_code": row.result_code,
                "result_description": row.result_description,
                "checked_at": row.checked_at.isoformat(),
            }
            for row in status_checks
        ],
    }


@router.get("/reconciliation/issues")
def list_reconciliation_issues(
    merchant_id: str | None = None,
    review_status: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:read")),
):
    query = _payments_query(db, principal).filter(
        (Payment.status.in_(["stk_sent", "unknown", "failed", "timeout"]))
        | (Payment.review_status == "needs_review")
    )
    if merchant_id:
        scoped_merchant(db, principal, merchant_id)
        query = query.filter(Payment.merchant_account_id == merchant_id)
    if review_status:
        query = query.filter(Payment.review_status == review_status)
    rows = query.order_by(Payment.created_at.desc()).limit(min(max(limit, 1), 500)).all()
    return {"items": [payment_payload(row) for row in rows]}


@router.get("/audit-logs")
def list_audit_logs(
    merchant_id: str | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    before: datetime | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("audit:read")),
):
    query = db.query(AuditLog).filter(AuditLog.organization_id == principal.organization_id)
    if principal.merchant_id:
        query = query.filter(AuditLog.merchant_account_id == principal.merchant_id)
    if merchant_id:
        scoped_merchant(db, principal, merchant_id)
        query = query.filter(AuditLog.merchant_account_id == merchant_id)
    if action:
        query = query.filter(AuditLog.action == action)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    if before:
        query = query.filter(AuditLog.created_at < before)
    page_size = min(max(limit, 1), 500)
    rows = query.order_by(AuditLog.created_at.desc()).limit(page_size).all()
    return {
        "items": [
            {
                "id": row.id,
                "merchant_id": row.merchant_account_id,
                "actor_user_id": row.actor_user_id,
                "actor_api_key_id": row.actor_api_key_id,
                "action": row.action,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "metadata": row.metadata_json,
                "ip_address": row.ip_address,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
        "next_before": rows[-1].created_at.isoformat() if len(rows) == page_size else None,
    }


@router.post("/payments/{payment_id}/retry")
async def retry_payment(
    payment_id: str,
    payload: PaymentRetryRequest,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:write")),
):
    payment = (
        _payments_query(db, principal).filter(Payment.id == payment_id).with_for_update().first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.status not in {"failed", "timeout", "unknown"}:
        raise HTTPException(
            status_code=409, detail=f"Payment cannot be retried from {payment.status}"
        )
    if payment.mpesa_receipt_number or payment.receipt_status in {"present", "enriched_later"}:
        raise HTTPException(
            status_code=409, detail="Successful receipt evidence blocks payment retry"
        )
    if payment.status == "failed" and payment.provider_acceptance_state != "rejected":
        raise HTTPException(
            status_code=409,
            detail="Only a failed request that Daraja definitely rejected or never received can be retried",
        )
    if payment.status in {"timeout", "unknown"}:
        if not payload.allow_uncertain:
            raise HTTPException(
                status_code=409,
                detail="Uncertain payment retry requires an explicit operator override",
            )
        if not principal.is_control_plane_admin:
            raise HTTPException(
                status_code=403,
                detail="Uncertain payment retry requires organization administrator access",
            )

    merchant = scoped_merchant(db, principal, payment.merchant_account_id)
    required_status = "verified" if payment.purpose == "merchant_verification" else "active"
    if merchant.status != required_status:
        raise HTTPException(status_code=409, detail="Merchant is not eligible for this retry")
    credential = active_credential(db, merchant)
    attempt_number = (
        db.query(PaymentAttempt).filter(PaymentAttempt.payment_id == payment.id).count() + 1
    )
    previous_checkout_request_id = payment.checkout_request_id
    begin_retry_and_record(
        db,
        payment=payment,
        principal=principal,
        request=request,
        attempt_number=attempt_number,
        reason=payload.reason,
    )
    payment.checkout_request_id = None
    payment.merchant_request_id = None
    payment.result_code = None
    payment.result_description = None
    payment.failed_at = None
    payment.next_reconciliation_at = None
    payment.provider_acceptance_state = "not_sent"
    payment.receipt_status = "missing"
    payment.review_status = "none"
    payment.review_reason = None
    attempt = PaymentAttempt(
        payment_id=payment.id,
        merchant_account_id=merchant.id,
        attempt_number=attempt_number,
        phone_number=payment.customer_phone,
        amount=payment.amount,
        request_payload_redacted={
            "phone_number": f"{payment.customer_phone[:6]}***{payment.customer_phone[-3:]}",
            "amount": str(payment.amount),
            "external_reference": payment.external_reference,
        },
        status="created",
        attempt_type="retry",
        retry_reason=payload.reason,
        initiated_by_user_id=principal.user_id,
        initiated_by_api_key_id=principal.api_key_id,
    )
    db.add(attempt)
    db.flush()
    audit(
        db,
        organization_id=payment.organization_id,
        merchant_id=merchant.id,
        action="payment_retry_attempt_created",
        entity_type="payment_attempt",
        entity_id=attempt.id,
        principal=principal,
        request=request,
        metadata={
            "attempt_number": attempt_number,
            "previous_checkout_request_id": previous_checkout_request_id,
        },
    )
    db.commit()
    return await _submit_stk_attempt(
        db=db,
        payment=payment,
        attempt=attempt,
        merchant=merchant,
        credential=credential,
        principal=principal,
        request=request,
    )


@router.post("/payments/{payment_id}/reconcile")
async def reconcile_payment_now(
    payment_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:write")),
):
    payment = _payments_query(db, principal).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    check = await reconcile_payment(db, payment.id)
    if not check:
        raise HTTPException(status_code=409, detail="Payment is not eligible for reconciliation")
    db.refresh(payment)
    return {
        "payment": payment_payload(payment),
        "status_check": {
            "id": check.id,
            "outcome": check.outcome,
            "result_code": check.result_code,
            "result_description": check.result_description,
            "checked_at": check.checked_at.isoformat(),
        },
    }


@router.get("/payments/{payment_id}/status-checks")
def list_payment_status_checks(
    payment_id: str,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:read")),
):
    payment = _payments_query(db, principal).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    rows = (
        db.query(PaymentStatusCheck)
        .filter(PaymentStatusCheck.payment_id == payment.id)
        .order_by(PaymentStatusCheck.checked_at.desc())
        .limit(min(max(limit, 1), 500))
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "outcome": row.outcome,
                "result_code": row.result_code,
                "result_description": row.result_description,
                "raw_response": row.raw_response,
                "checked_at": row.checked_at.isoformat(),
            }
            for row in rows
        ]
    }


def _callback_allowed(request: Request) -> bool:
    if settings.mpesa_callback_hmac:
        return verify_callback_signature(
            request.state.lynxpay_raw_callback,
            request.headers.get("X-Safaricom-Signature"),
        )
    allowlist = settings.MPESA_CALLBACK_IP_ALLOWLIST.strip()
    return not allowlist or ip_in_cidrs(
        get_client_ip(request), allowlist, _label="MPESA_CALLBACK_IP_ALLOWLIST"
    )


async def _read_callback_body(request: Request) -> tuple[bytes, bool]:
    """Read at most the configured callback limit while draining the request stream."""

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


def _normalized_callback_evidence(fields: dict) -> tuple[Decimal | None, str | None]:
    amount = None
    if fields["amount"] is not None:
        with suppress(InvalidOperation, ValueError):
            amount = Decimal(str(fields["amount"])).quantize(Decimal("0.01"))
    phone = None
    if fields["phone"] is not None:
        with suppress(ValueError):
            phone = normalize_kenyan_phone(fields["phone"])
    return amount, phone


@router.post("/callbacks/mpesa/{merchant_id}")
async def receive_mpesa_callback(merchant_id: str, request: Request, db: Session = Depends(get_db)):
    merchant = db.query(MerchantAccount).filter(MerchantAccount.id == merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")
    set_tenant_context(db, merchant.organization_id)
    raw, oversized = await _read_callback_body(request)
    request.state.lynxpay_raw_callback = raw
    raw_text = raw.decode("utf-8", errors="replace")
    if oversized:
        payload = {"_truncated": raw_text, "_limit_bytes": settings.MAX_CALLBACK_BODY_BYTES}
    else:
        try:
            payload = json.loads(raw_text)
            if not isinstance(payload, dict):
                payload = {"_value": payload}
        except json.JSONDecodeError:
            payload = {"_unparsed": raw_text}
    fields = callback_fields(payload)
    callback_amount, callback_phone = _normalized_callback_evidence(fields)
    callback = MpesaCallback(
        merchant_account_id=merchant.id,
        checkout_request_id=fields["checkout_request_id"],
        merchant_request_id=fields["merchant_request_id"],
        mpesa_receipt_number=fields["mpesa_receipt_number"],
        callback_amount=callback_amount,
        callback_phone=callback_phone,
        result_code=fields["result_code"],
        result_description=fields["result_description"],
        raw_payload=payload,
        raw_body=raw_text,
        processed=False,
        processing_status="received",
        source_ip=get_client_ip(request),
    )
    db.add(callback)
    db.flush()
    audit(
        db,
        organization_id=merchant.organization_id,
        merchant_id=merchant.id,
        action="callback_received",
        entity_type="mpesa_callback",
        entity_id=callback.id,
        request=request,
        metadata={
            "checkout_request_id": fields["checkout_request_id"],
            "result_code": fields["result_code"],
        },
    )
    db.commit()  # Preserve the raw callback before any validation or payment mutation.
    CALLBACKS_RECEIVED.inc()
    # PostgreSQL set_config(..., true) is transaction-local; restore it after
    # the raw-first durability boundary before touching RLS-protected payment data.
    set_tenant_context(db, merchant.organization_id)

    if oversized:
        callback.processing_status = "oversized"
        callback.processed = True
        callback.processed_at = utcnow()
        audit(
            db,
            organization_id=merchant.organization_id,
            merchant_id=merchant.id,
            action="callback_oversized_rejected",
            entity_type="mpesa_callback",
            entity_id=callback.id,
            request=request,
            metadata={"limit_bytes": settings.MAX_CALLBACK_BODY_BYTES},
        )
        db.commit()
        CALLBACKS_PROCESSED.labels("oversized").inc()
        raise HTTPException(status_code=413, detail="M-PESA callback body is too large")

    if not _callback_allowed(request):
        callback.processing_status = "source_rejected"
        callback.processed = True
        callback.processed_at = utcnow()
        audit(
            db,
            organization_id=merchant.organization_id,
            merchant_id=merchant.id,
            action="callback_source_rejected",
            entity_type="mpesa_callback",
            entity_id=callback.id,
            request=request,
        )
        db.commit()
        CALLBACKS_PROCESSED.labels("source_rejected").inc()
        return {"ResultCode": 0, "ResultDesc": "Accepted"}
    if not fields["checkout_request_id"] or fields["result_code"] is None:
        callback.processing_status = "malformed"
        callback.processed = True
        callback.processed_at = utcnow()
        audit(
            db,
            organization_id=merchant.organization_id,
            merchant_id=merchant.id,
            action="callback_malformed",
            entity_type="mpesa_callback",
            entity_id=callback.id,
            request=request,
        )
        db.commit()
        CALLBACKS_PROCESSED.labels("malformed").inc()
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    matched_attempt = (
        db.query(PaymentAttempt)
        .filter(
            PaymentAttempt.merchant_account_id == merchant.id,
            PaymentAttempt.checkout_request_id == fields["checkout_request_id"],
        )
        .first()
    )
    payment_query = db.query(Payment).filter(Payment.merchant_account_id == merchant.id)
    if matched_attempt:
        payment_query = payment_query.filter(Payment.id == matched_attempt.payment_id)
    else:
        payment_query = payment_query.filter(
            Payment.checkout_request_id == fields["checkout_request_id"]
        )
    payment = payment_query.with_for_update().first()
    if not payment:
        callback.processing_status = "unmatched"
        callback.processed = True
        callback.processed_at = utcnow()
        audit(
            db,
            organization_id=merchant.organization_id,
            merchant_id=merchant.id,
            action="callback_unmatched",
            entity_type="mpesa_callback",
            entity_id=callback.id,
            request=request,
        )
        db.commit()
        CALLBACKS_PROCESSED.labels("unmatched").inc()
        return {"ResultCode": 0, "ResultDesc": "Accepted"}
    callback.payment_id = payment.id

    if fields["result_code"] == "0" and fields["mpesa_receipt_number"]:
        previous = (
            db.query(MpesaCallback)
            .filter(
                MpesaCallback.id != callback.id,
                MpesaCallback.merchant_account_id == merchant.id,
                MpesaCallback.checkout_request_id == fields["checkout_request_id"],
                MpesaCallback.mpesa_receipt_number == fields["mpesa_receipt_number"],
                MpesaCallback.callback_amount == callback_amount,
                MpesaCallback.callback_phone == callback_phone,
                MpesaCallback.processing_status == "processed_success",
            )
            .order_by(MpesaCallback.received_at.asc())
            .first()
        )
        if previous:
            callback.duplicate_of_callback_id = previous.id
            callback.processing_status = "duplicate"
            callback.processed = True
            callback.processed_at = utcnow()
            audit(
                db,
                organization_id=merchant.organization_id,
                merchant_id=merchant.id,
                action="duplicate_callback_detected",
                entity_type="mpesa_callback",
                entity_id=callback.id,
                request=request,
                metadata={"duplicate_of": previous.id},
            )
            db.commit()
            CALLBACKS_DUPLICATE.inc()
            CALLBACKS_PROCESSED.labels("duplicate").inc()
            return {"ResultCode": 0, "ResultDesc": "Accepted"}
    elif fields["result_code"] != "0":
        previous = (
            db.query(MpesaCallback)
            .filter(
                MpesaCallback.id != callback.id,
                MpesaCallback.merchant_account_id == merchant.id,
                MpesaCallback.checkout_request_id == fields["checkout_request_id"],
                MpesaCallback.result_code == fields["result_code"],
                MpesaCallback.result_description == fields["result_description"],
                MpesaCallback.processing_status.in_(["processed_failure", "processed_unknown"]),
            )
            .order_by(MpesaCallback.received_at.asc())
            .first()
        )
        if previous:
            callback.duplicate_of_callback_id = previous.id
            callback.processing_status = "duplicate"
            callback.processed = True
            callback.processed_at = utcnow()
            audit(
                db,
                organization_id=merchant.organization_id,
                merchant_id=merchant.id,
                action="duplicate_callback_detected",
                entity_type="mpesa_callback",
                entity_id=callback.id,
                request=request,
                metadata={"duplicate_of": previous.id},
            )
            db.commit()
            CALLBACKS_DUPLICATE.inc()
            CALLBACKS_PROCESSED.labels("duplicate").inc()
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

    event_type = None
    try:
        if fields["result_code"] == "0":
            duplicate_receipt = (
                db.query(Payment)
                .filter(
                    Payment.merchant_account_id == merchant.id,
                    Payment.mpesa_receipt_number == fields["mpesa_receipt_number"],
                    Payment.id != payment.id,
                )
                .first()
                if fields["mpesa_receipt_number"]
                else None
            )
            if duplicate_receipt:
                callback.processing_status = "conflict"
                payment.review_status = "needs_review"
                payment.review_reason = "duplicate_mpesa_receipt"
                if matched_attempt:
                    matched_attempt.status = "conflict"
                audit(
                    db,
                    organization_id=merchant.organization_id,
                    merchant_id=merchant.id,
                    action="duplicate_mpesa_receipt_detected",
                    entity_type="mpesa_callback",
                    entity_id=callback.id,
                    request=request,
                    metadata={"existing_payment_id": duplicate_receipt.id},
                )
            elif (
                payment.status == "success"
                and payment.mpesa_receipt_number == fields["mpesa_receipt_number"]
            ):
                callback.duplicate_of_callback_id = (
                    db.query(MpesaCallback.id)
                    .filter(
                        MpesaCallback.payment_id == payment.id, MpesaCallback.processed.is_(True)
                    )
                    .order_by(MpesaCallback.received_at.asc())
                    .scalar()
                )
                callback.processing_status = "duplicate"
                audit(
                    db,
                    organization_id=merchant.organization_id,
                    merchant_id=merchant.id,
                    action="duplicate_callback_detected",
                    entity_type="mpesa_callback",
                    entity_id=callback.id,
                    request=request,
                )
            elif payment.status == "success" and payment.mpesa_receipt_number:
                callback.processing_status = "conflict"
                payment.review_status = "needs_review"
                payment.review_reason = "conflicting_success_receipt"
                if matched_attempt:
                    matched_attempt.status = "conflict"
                audit(
                    db,
                    organization_id=merchant.organization_id,
                    merchant_id=merchant.id,
                    action="callback_conflict_detected",
                    entity_type="mpesa_callback",
                    entity_id=callback.id,
                    request=request,
                    metadata={
                        "existing_receipt": payment.mpesa_receipt_number,
                        "received_receipt": fields["mpesa_receipt_number"],
                    },
                )
            elif payment.status == "success" and not payment.mpesa_receipt_number:
                matches, reason = callback_matches_payment(payment, fields)
                if matches:
                    payment.mpesa_receipt_number = fields["mpesa_receipt_number"]
                    payment.result_code = fields["result_code"]
                    payment.result_description = fields["result_description"]
                    payment.success_source = "callback"
                    payment.receipt_status = "enriched_later"
                    payment.review_status = "resolved"
                    payment.review_reason = None
                    payment.provider_acceptance_state = "accepted"
                    if matched_attempt:
                        matched_attempt.status = "succeeded"
                    callback.processing_status = "processed_success"
                    audit(
                        db,
                        organization_id=merchant.organization_id,
                        merchant_id=merchant.id,
                        action="payment_success_evidence_enriched",
                        entity_type="payment",
                        entity_id=payment.id,
                        request=request,
                        metadata={
                            "callback_id": callback.id,
                            "receipt": fields["mpesa_receipt_number"],
                        },
                    )
                else:
                    callback.processing_status = "verification_failed"
                    audit(
                        db,
                        organization_id=merchant.organization_id,
                        merchant_id=merchant.id,
                        action="callback_verification_failed",
                        entity_type="mpesa_callback",
                        entity_id=callback.id,
                        request=request,
                        metadata={"reason": reason},
                    )
            else:
                matches, reason = callback_matches_payment(payment, fields)
                if matches:
                    transition_and_record(
                        db,
                        payment=payment,
                        target="success",
                        event_type="payment.success",
                        request=request,
                        details={
                            "callback_id": callback.id,
                            "receipt": fields["mpesa_receipt_number"],
                        },
                    )
                    payment.mpesa_receipt_number = fields["mpesa_receipt_number"]
                    payment.result_code = fields["result_code"]
                    payment.result_description = fields["result_description"]
                    payment.paid_at = utcnow()
                    payment.success_source = "callback"
                    payment.receipt_status = "present"
                    payment.review_status = "none"
                    payment.review_reason = None
                    payment.provider_acceptance_state = "accepted"
                    if matched_attempt:
                        matched_attempt.status = "succeeded"
                    event_type = "payment.success"
                    callback.processing_status = "processed_success"
                else:
                    if payment.status == "stk_sent":
                        transition_and_record(
                            db,
                            payment=payment,
                            target="unknown",
                            event_type="payment.unknown",
                            request=request,
                            details={"callback_id": callback.id, "reason": reason},
                        )
                        event_type = "payment.unknown"
                    payment.review_status = "needs_review"
                    payment.review_reason = reason
                    payment.provider_acceptance_state = "accepted"
                    callback.processing_status = "verification_failed"
                    audit(
                        db,
                        organization_id=merchant.organization_id,
                        merchant_id=merchant.id,
                        action="callback_verification_failed",
                        entity_type="mpesa_callback",
                        entity_id=callback.id,
                        request=request,
                        metadata={"reason": reason},
                    )
        else:
            classified = classify_mpesa_result(fields["result_code"])
            target = (
                "failed"
                if payment.status == "unknown" and classified.target == "timeout"
                else classified.target
            )
            if target != "unknown" or payment.status != "unknown":
                transition_and_record(
                    db,
                    payment=payment,
                    target=target,
                    event_type=f"payment.{target}",
                    request=request,
                    details={
                        "callback_id": callback.id,
                        "result_code": fields["result_code"],
                        "provider_category": classified.category,
                    },
                )
            payment.result_code = fields["result_code"]
            payment.result_description = fields["result_description"]
            payment.failed_at = utcnow() if target in {"failed", "timeout"} else None
            payment.provider_acceptance_state = "accepted"
            payment.receipt_status = "not_applicable" if target != "unknown" else "missing"
            payment.review_status = "needs_review" if classified.needs_review else "none"
            payment.review_reason = (
                f"provider_result_{classified.category}" if classified.needs_review else None
            )
            if matched_attempt:
                matched_attempt.status = "uncertain" if target == "unknown" else target
            event_type = f"payment.{target}"
            callback.processing_status = (
                "processed_unknown" if target == "unknown" else "processed_failure"
            )
    except InvalidPaymentTransitionError as exc:
        callback.processing_status = "conflict"
        payment.review_status = "needs_review"
        payment.review_reason = "callback_transition_conflict"
        if matched_attempt:
            matched_attempt.status = "conflict"
        audit(
            db,
            organization_id=merchant.organization_id,
            merchant_id=merchant.id,
            action="callback_transition_blocked",
            entity_type="mpesa_callback",
            entity_id=callback.id,
            request=request,
            metadata={"reason": str(exc), "payment_status": payment.status},
        )
    callback.processed = True
    callback.processed_at = utcnow()
    if event_type:
        queue_webhooks(db, payment, event_type)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        audit(
            db,
            organization_id=merchant.organization_id,
            merchant_id=merchant.id,
            action="callback_unique_constraint_blocked",
            entity_type="mpesa_callback",
            entity_id=callback.id,
            request=request,
        )
        db.commit()
    CALLBACKS_PROCESSED.labels(callback.processing_status).inc()
    if event_type:
        PAYMENT_OUTCOMES.labels(event_type.removeprefix("payment."), "callback").inc()
    return {"ResultCode": 0, "ResultDesc": "Accepted"}


def _callbacks_query(db: Session, principal: Principal):
    query = (
        db.query(MpesaCallback)
        .join(MerchantAccount, MerchantAccount.id == MpesaCallback.merchant_account_id)
        .filter(MerchantAccount.organization_id == principal.organization_id)
    )
    if principal.merchant_id:
        query = query.filter(MpesaCallback.merchant_account_id == principal.merchant_id)
    elif principal.api_key_id and principal.environment:
        query = query.filter(MerchantAccount.environment == principal.environment)
    return query


@router.get("/callbacks")
def list_callbacks(
    merchant_id: str | None = None,
    processing_status: str | None = None,
    before: datetime | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("callbacks:read")),
):
    query = _callbacks_query(db, principal)
    if merchant_id:
        scoped_merchant(db, principal, merchant_id)
        query = query.filter(MpesaCallback.merchant_account_id == merchant_id)
    if processing_status:
        query = query.filter(MpesaCallback.processing_status == processing_status)
    if before:
        query = query.filter(MpesaCallback.received_at < before)
    page_size = min(max(limit, 1), 500)
    records = query.order_by(MpesaCallback.received_at.desc()).limit(page_size).all()
    include_raw = bool(
        principal.user_id
        and "callbacks:read_raw" in principal.scopes
        and (not settings.REQUIRE_PRIVILEGED_MFA or principal.mfa_authenticated)
    )
    return {
        "items": [callback_view(item, include_raw=include_raw) for item in records],
        "next_before": records[-1].received_at.isoformat() if len(records) == page_size else None,
    }


@router.get("/callbacks/{callback_id}")
def get_callback(
    callback_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("callbacks:read")),
):
    callback = _callbacks_query(db, principal).filter(MpesaCallback.id == callback_id).first()
    if not callback:
        raise HTTPException(status_code=404, detail="Callback not found")
    include_raw = bool(
        principal.user_id
        and "callbacks:read_raw" in principal.scopes
        and (not settings.REQUIRE_PRIVILEGED_MFA or principal.mfa_authenticated)
    )
    return callback_view(callback, include_raw=include_raw)


@router.post("/webhooks/endpoints", status_code=201)
def create_webhook_endpoint(
    payload: WebhookEndpointCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("webhooks:write")),
):
    if principal.api_key_id and not payload.merchant_id:
        raise HTTPException(
            status_code=422, detail="API-key webhook endpoints must be bound to one merchant"
        )
    if payload.merchant_id:
        merchant = scoped_merchant(db, principal, payload.merchant_id)
        environment = merchant.environment
    else:
        environment = "production" if str(payload.url).startswith("https://") else "sandbox"
    _ensure_https(str(payload.url), environment)
    try:
        validate_webhook_url(str(payload.url))
    except UnsafeWebhookUrlError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    signing_secret = f"whsec_{secrets.token_urlsafe(32)}"
    encrypted_secret = encrypt_sensitive_value(signing_secret)
    endpoint = WebhookEndpoint(
        organization_id=principal.organization_id,
        merchant_account_id=payload.merchant_id,
        url=str(payload.url),
        event_types=payload.event_types,
        secret_encrypted=encrypted_secret,
        encryption_key_version=encryption_key_version(encrypted_secret),
        status="active",
    )
    db.add(endpoint)
    db.flush()
    audit(
        db,
        organization_id=principal.organization_id,
        merchant_id=payload.merchant_id,
        action="webhook_endpoint_created",
        entity_type="webhook_endpoint",
        entity_id=endpoint.id,
        principal=principal,
        request=request,
        metadata={"event_types": payload.event_types},
    )
    db.commit()
    return {
        "id": endpoint.id,
        "merchant_id": endpoint.merchant_account_id,
        "url": endpoint.url,
        "event_types": endpoint.event_types,
        "status": endpoint.status,
        "signing_secret": signing_secret,
        "warning": "This signing secret is shown once and cannot be recovered.",
    }


def _webhook_endpoint_view(endpoint: WebhookEndpoint) -> dict:
    return {
        "id": endpoint.id,
        "merchant_id": endpoint.merchant_account_id,
        "url": endpoint.url,
        "event_types": endpoint.event_types,
        "status": endpoint.status,
        "consecutive_failures": endpoint.consecutive_failures,
        "paused_at": endpoint.paused_at.isoformat() if endpoint.paused_at else None,
        "pause_reason": endpoint.pause_reason,
        "created_at": endpoint.created_at.isoformat() if endpoint.created_at else None,
        "updated_at": endpoint.updated_at.isoformat() if endpoint.updated_at else None,
    }


def _webhook_delivery_view(delivery: WebhookDelivery, *, include_response: bool = False) -> dict:
    view = {
        "id": delivery.id,
        "endpoint_id": delivery.webhook_endpoint_id,
        "payment_id": delivery.payment_id,
        "event_type": delivery.event_type,
        "status": delivery.status,
        "attempts": delivery.attempts,
        "max_attempts": delivery.max_attempts,
        "response_status_code": delivery.response_status_code,
        "last_error": delivery.last_error,
        "next_retry_at": delivery.next_retry_at.isoformat() if delivery.next_retry_at else None,
        "last_attempt_at": delivery.last_attempt_at.isoformat()
        if delivery.last_attempt_at
        else None,
        "delivered_at": delivery.delivered_at.isoformat() if delivery.delivered_at else None,
        "created_at": delivery.created_at.isoformat() if delivery.created_at else None,
    }
    if include_response:
        view["response_body"] = delivery.response_body
        view["payload"] = delivery.payload
    return view


def _webhook_endpoints_query(db: Session, principal: Principal):
    query = db.query(WebhookEndpoint).filter(
        WebhookEndpoint.organization_id == principal.organization_id
    )
    if principal.merchant_id:
        query = query.filter(WebhookEndpoint.merchant_account_id == principal.merchant_id)
    elif principal.api_key_id and principal.environment:
        query = query.join(
            MerchantAccount, MerchantAccount.id == WebhookEndpoint.merchant_account_id
        ).filter(MerchantAccount.environment == principal.environment)
    return query


@router.get("/webhooks/endpoints")
def list_webhook_endpoints(
    before: datetime | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("webhooks:read")),
):
    query = _webhook_endpoints_query(db, principal).filter(WebhookEndpoint.status != "archived")
    if before:
        query = query.filter(WebhookEndpoint.created_at < before)
    page_size = min(max(limit, 1), 500)
    rows = query.order_by(WebhookEndpoint.created_at.desc()).limit(page_size).all()
    return {
        "items": [_webhook_endpoint_view(row) for row in rows],
        "next_before": rows[-1].created_at.isoformat() if len(rows) == page_size else None,
    }


@router.patch("/webhooks/endpoints/{endpoint_id}")
def update_webhook_endpoint(
    endpoint_id: str,
    payload: WebhookEndpointUpdate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("webhooks:write")),
):
    endpoint = (
        _webhook_endpoints_query(db, principal).filter(WebhookEndpoint.id == endpoint_id).first()
    )
    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    changes = payload.model_dump(exclude_unset=True)
    if "url" in changes:
        new_url = str(changes["url"])
        environment = "production" if settings.is_production else "sandbox"
        if endpoint.merchant_account_id:
            environment = scoped_merchant(db, principal, endpoint.merchant_account_id).environment
        _ensure_https(new_url, environment)
        try:
            validate_webhook_url(new_url)
        except UnsafeWebhookUrlError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        changes["url"] = new_url
    for field, value in changes.items():
        setattr(endpoint, field, value)
    if changes.get("status") == "active":
        endpoint.consecutive_failures = 0
        endpoint.paused_at = None
        endpoint.pause_reason = None
    audit(
        db,
        organization_id=principal.organization_id,
        merchant_id=endpoint.merchant_account_id,
        action="webhook_endpoint_updated",
        entity_type="webhook_endpoint",
        entity_id=endpoint.id,
        principal=principal,
        request=request,
        metadata={"changed_fields": list(changes)},
    )
    db.commit()
    return _webhook_endpoint_view(endpoint)


@router.delete("/webhooks/endpoints/{endpoint_id}", status_code=204)
def archive_webhook_endpoint(
    endpoint_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("webhooks:write")),
):
    endpoint = (
        _webhook_endpoints_query(db, principal).filter(WebhookEndpoint.id == endpoint_id).first()
    )
    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    endpoint.status = "archived"
    audit(
        db,
        organization_id=principal.organization_id,
        merchant_id=endpoint.merchant_account_id,
        action="webhook_endpoint_archived",
        entity_type="webhook_endpoint",
        entity_id=endpoint.id,
        principal=principal,
        request=request,
    )
    db.commit()
    return None


@router.post("/webhooks/endpoints/{endpoint_id}/rotate-secret")
def rotate_webhook_secret(
    endpoint_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("webhooks:write")),
):
    endpoint = (
        _webhook_endpoints_query(db, principal)
        .filter(WebhookEndpoint.id == endpoint_id, WebhookEndpoint.status != "archived")
        .first()
    )
    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    signing_secret = f"whsec_{secrets.token_urlsafe(32)}"
    encrypted = encrypt_sensitive_value(signing_secret)
    endpoint.secret_encrypted = encrypted
    endpoint.encryption_key_version = encryption_key_version(encrypted)
    audit(
        db,
        organization_id=principal.organization_id,
        merchant_id=endpoint.merchant_account_id,
        action="webhook_secret_rotated",
        entity_type="webhook_endpoint",
        entity_id=endpoint.id,
        principal=principal,
        request=request,
    )
    db.commit()
    return {
        "id": endpoint.id,
        "signing_secret": signing_secret,
        "warning": "This signing secret is shown once and cannot be recovered.",
    }


@router.post("/webhooks/endpoints/{endpoint_id}/test", status_code=201)
def test_webhook_endpoint(
    endpoint_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("webhooks:write")),
):
    endpoint = (
        _webhook_endpoints_query(db, principal)
        .filter(WebhookEndpoint.id == endpoint_id, WebhookEndpoint.status == "active")
        .first()
    )
    if not endpoint:
        raise HTTPException(status_code=404, detail="Active webhook endpoint not found")
    delivery = WebhookDelivery(
        webhook_endpoint_id=endpoint.id,
        event_type="webhook.test",
        payload={
            "id": f"evt_{uuid.uuid4()}",
            "event": "webhook.test",
            "created_at": utcnow().isoformat(),
            "data": {"message": "LynxPay test delivery"},
        },
        status="queued",
        attempts=0,
        max_attempts=settings.WEBHOOK_MAX_ATTEMPTS,
        next_retry_at=utcnow(),
    )
    db.add(delivery)
    db.flush()
    audit(
        db,
        organization_id=principal.organization_id,
        merchant_id=endpoint.merchant_account_id,
        action="webhook_test_queued",
        entity_type="webhook_delivery",
        entity_id=delivery.id,
        principal=principal,
        request=request,
    )
    db.commit()
    return _webhook_delivery_view(delivery)


@router.get("/webhooks/deliveries")
def list_webhook_deliveries(
    endpoint_id: str | None = None,
    status: str | None = None,
    before: datetime | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("webhooks:read")),
):
    query = db.query(WebhookDelivery).join(
        WebhookEndpoint, WebhookEndpoint.id == WebhookDelivery.webhook_endpoint_id
    )
    allowed_endpoints = _webhook_endpoints_query(db, principal).with_entities(WebhookEndpoint.id)
    query = query.filter(WebhookDelivery.webhook_endpoint_id.in_(allowed_endpoints))
    if endpoint_id:
        query = query.filter(WebhookDelivery.webhook_endpoint_id == endpoint_id)
    if status:
        query = query.filter(WebhookDelivery.status == status)
    if before:
        query = query.filter(WebhookDelivery.created_at < before)
    page_size = min(max(limit, 1), 500)
    rows = query.order_by(WebhookDelivery.created_at.desc()).limit(page_size).all()
    return {
        "items": [_webhook_delivery_view(row) for row in rows],
        "next_before": rows[-1].created_at.isoformat() if len(rows) == page_size else None,
    }


@router.get("/webhooks/deliveries/{delivery_id}")
def get_webhook_delivery(
    delivery_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("webhooks:read")),
):
    allowed_endpoints = _webhook_endpoints_query(db, principal).with_entities(WebhookEndpoint.id)
    delivery = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.webhook_endpoint_id.in_(allowed_endpoints),
        )
        .first()
    )
    if not delivery:
        raise HTTPException(status_code=404, detail="Webhook delivery not found")
    view = _webhook_delivery_view(delivery, include_response=True)
    attempts = (
        db.query(WebhookDeliveryAttempt)
        .filter(WebhookDeliveryAttempt.webhook_delivery_id == delivery.id)
        .order_by(WebhookDeliveryAttempt.attempt_number.asc())
        .all()
    )
    view["delivery_attempts"] = [
        {
            "id": row.id,
            "attempt_number": row.attempt_number,
            "status": row.status,
            "response_status_code": row.response_status_code,
            "response_body": row.response_body,
            "error": row.error,
            "started_at": row.started_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }
        for row in attempts
    ]
    return view


@router.post("/webhooks/deliveries/{delivery_id}/replay", status_code=201)
def replay_webhook_delivery(
    delivery_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("webhooks:write")),
):
    allowed_endpoints = _webhook_endpoints_query(db, principal).with_entities(WebhookEndpoint.id)
    original = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.webhook_endpoint_id.in_(allowed_endpoints),
        )
        .first()
    )
    if not original:
        raise HTTPException(status_code=404, detail="Webhook delivery not found")
    endpoint = (
        db.query(WebhookEndpoint).filter(WebhookEndpoint.id == original.webhook_endpoint_id).first()
    )
    replay = WebhookDelivery(
        webhook_endpoint_id=original.webhook_endpoint_id,
        payment_id=original.payment_id,
        replay_of_delivery_id=original.id,
        event_type=original.event_type,
        payload=original.payload,
        status="queued",
        attempts=0,
        max_attempts=settings.WEBHOOK_MAX_ATTEMPTS,
        next_retry_at=utcnow(),
    )
    db.add(replay)
    db.flush()
    audit(
        db,
        organization_id=principal.organization_id,
        merchant_id=endpoint.merchant_account_id,
        action="webhook_replay_requested",
        entity_type="webhook_delivery",
        entity_id=replay.id,
        principal=principal,
        request=request,
        metadata={"replay_of": original.id},
    )
    db.commit()
    return {"id": replay.id, "status": replay.status, "replay_of_delivery_id": original.id}
