"""LynxPay platform-admin production approval endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database import get_admin_db
from app.deps import Principal, require_platform_admin
from app.models import DarajaCredential, MerchantAccount, Organization, Payment, User
from app.service import audit, utcnow

router = APIRouter(prefix="/admin", tags=["Platform administration"])


class AdminDecision(BaseModel):
    reason: str = Field(min_length=8, max_length=500)


def _view(merchant: MerchantAccount, organization: Organization) -> dict:
    return {
        "id": merchant.id,
        "organization_id": merchant.organization_id,
        "organization_name": organization.name,
        "organization_email": organization.contact_email,
        "merchant_name": merchant.merchant_name,
        "shortcode": merchant.shortcode,
        "shortcode_type": merchant.shortcode_type,
        "environment": merchant.environment,
        "status": merchant.status,
        "approval_submitted_at": merchant.approval_submitted_at.isoformat()
        if merchant.approval_submitted_at
        else None,
        "approved_at": merchant.approved_at.isoformat() if merchant.approved_at else None,
        "rejected_at": merchant.rejected_at.isoformat() if merchant.rejected_at else None,
        "rejection_reason": merchant.rejection_reason,
        "suspended_at": merchant.suspended_at.isoformat() if merchant.suspended_at else None,
    }


def _merchant(db: Session, merchant_id: str) -> tuple[MerchantAccount, Organization]:
    row = (
        db.query(MerchantAccount, Organization)
        .join(Organization, Organization.id == MerchantAccount.organization_id)
        .filter(MerchantAccount.id == merchant_id)
        .with_for_update()
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Merchant not found")
    return row


def _approval_evidence(db: Session, merchant: MerchantAccount, organization: Organization) -> dict:
    if merchant.environment != "production" or merchant.status != "pending_approval":
        raise HTTPException(status_code=409, detail="Merchant is not awaiting production approval")
    if (
        organization.accepted_terms_version != settings.TERMS_VERSION
        or organization.accepted_privacy_version != settings.PRIVACY_VERSION
        or not organization.terms_accepted_at
        or not organization.privacy_accepted_at
    ):
        raise HTTPException(status_code=409, detail="Merchant consent evidence is incomplete")
    verified_owner = (
        db.query(User)
        .filter(
            User.organization_id == merchant.organization_id,
            User.role == "owner",
            User.status == "active",
            User.email_verified_at.isnot(None),
        )
        .first()
    )
    if not verified_owner:
        raise HTTPException(status_code=409, detail="A verified organization owner is required")
    credential = (
        db.query(DarajaCredential)
        .filter(
            DarajaCredential.merchant_account_id == merchant.id,
            DarajaCredential.environment == "production",
            DarajaCredential.is_active.is_(True),
            DarajaCredential.last_tested_at.isnot(None),
        )
        .first()
    )
    if not credential:
        raise HTTPException(status_code=409, detail="Verified production credentials are required")
    verification_payment = (
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
    if not verification_payment:
        raise HTTPException(status_code=409, detail="Successful KES 1 verification is required")
    return {
        "verified_owner_id": verified_owner.id,
        "credential_id": credential.id,
        "verification_payment_id": verification_payment.id,
        "terms_version": organization.accepted_terms_version,
        "privacy_version": organization.accepted_privacy_version,
    }


@router.get("/merchants/pending-approval")
def pending_approval_merchants(
    limit: int = 50,
    before: datetime | None = None,
    db: Session = Depends(get_admin_db),
    _principal: Principal = Depends(require_platform_admin),
):
    query = (
        db.query(MerchantAccount, Organization)
        .join(Organization, Organization.id == MerchantAccount.organization_id)
        .filter(
            MerchantAccount.environment == "production",
            MerchantAccount.status == "pending_approval",
        )
    )
    if before:
        query = query.filter(MerchantAccount.created_at < before)
    rows = query.order_by(MerchantAccount.created_at.desc()).limit(min(max(limit, 1), 100)).all()
    return {
        "items": [_view(merchant, organization) for merchant, organization in rows],
        "next_before": rows[-1][0].created_at.isoformat()
        if len(rows) == min(max(limit, 1), 100)
        else None,
    }


@router.post("/merchants/{merchant_id}/approve")
def approve_merchant(
    merchant_id: str,
    payload: AdminDecision,
    request: Request,
    db: Session = Depends(get_admin_db),
    principal: Principal = Depends(require_platform_admin),
):
    merchant, organization = _merchant(db, merchant_id)
    if principal.organization_id == merchant.organization_id:
        raise HTTPException(status_code=403, detail="Production approval must be independent")
    evidence = _approval_evidence(db, merchant, organization)
    merchant.status = "active"
    merchant.approved_at = utcnow()
    merchant.approved_by_user_id = principal.user_id
    merchant.rejected_at = None
    merchant.rejection_reason = None
    audit(
        db,
        organization_id=merchant.organization_id,
        merchant_id=merchant.id,
        action="production_merchant_approved",
        entity_type="merchant_account",
        entity_id=merchant.id,
        principal=principal,
        request=request,
        metadata={"reason": payload.reason, **evidence},
    )
    db.commit()
    return _view(merchant, organization)


@router.post("/merchants/{merchant_id}/reject")
def reject_merchant(
    merchant_id: str,
    payload: AdminDecision,
    request: Request,
    db: Session = Depends(get_admin_db),
    principal: Principal = Depends(require_platform_admin),
):
    merchant, organization = _merchant(db, merchant_id)
    if merchant.environment != "production" or merchant.status != "pending_approval":
        raise HTTPException(status_code=409, detail="Merchant is not awaiting production approval")
    merchant.status = "rejected"
    merchant.rejected_at = utcnow()
    merchant.rejection_reason = payload.reason
    audit(
        db,
        organization_id=merchant.organization_id,
        merchant_id=merchant.id,
        action="production_merchant_rejected",
        entity_type="merchant_account",
        entity_id=merchant.id,
        principal=principal,
        request=request,
        metadata={"reason": payload.reason},
    )
    db.commit()
    return _view(merchant, organization)


@router.post("/merchants/{merchant_id}/suspend")
def suspend_merchant(
    merchant_id: str,
    payload: AdminDecision,
    request: Request,
    db: Session = Depends(get_admin_db),
    principal: Principal = Depends(require_platform_admin),
):
    merchant, organization = _merchant(db, merchant_id)
    if merchant.status == "suspended":
        raise HTTPException(status_code=409, detail="Merchant is already suspended")
    merchant.status = "suspended"
    merchant.suspended_at = utcnow()
    audit(
        db,
        organization_id=merchant.organization_id,
        merchant_id=merchant.id,
        action="merchant_suspended",
        entity_type="merchant_account",
        entity_id=merchant.id,
        principal=principal,
        request=request,
        metadata={"reason": payload.reason},
    )
    db.commit()
    return _view(merchant, organization)
