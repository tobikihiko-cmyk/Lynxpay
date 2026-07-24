"""LynxPay domain HTTP routes."""

from __future__ import annotations

from datetime import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_db
from app.deps import (
    Principal,
    require_control_admin,
    require_scope,
    scoped_merchant,
)
from app.models import (
    MerchantAccount,
    Organization,
    Payment,
    User,
)
from app.schemas import (
    MerchantCreate,
    MerchantUpdate,
)
from app.service import (
    active_credential,
    audit,
    utcnow,
)

router = APIRouter(tags=["LynxPay"])


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


def _ensure_https(url: str, environment: str) -> None:
    if environment == "production" and not url.lower().startswith("https://"):
        raise HTTPException(
            status_code=422, detail="Production callback and webhook URLs must use HTTPS"
        )


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
    if settings.public_url:
        callback_url = f"{settings.public_url}/api/v1/callbacks/mpesa/{merchant_id}"
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
        if settings.public_url:
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
