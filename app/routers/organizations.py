"""LynxPay domain HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_db
from app.deps import (
    Principal,
    require_control_admin,
)
from app.models import (
    Organization,
)
from app.schemas import (
    ConsentAcceptance,
    OrganizationUpdate,
)
from app.service import (
    audit,
    utcnow,
)

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
