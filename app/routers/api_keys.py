"""LynxPay domain HTTP routes."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.deps import (
    Principal,
    require_control_admin,
    scoped_merchant,
)
from app.models import (
    ApiKey,
)
from app.schemas import (
    ApiKeyCreate,
)
from app.security import generate_api_key
from app.service import (
    audit,
    utcnow,
)

router = APIRouter(tags=["LynxPay"])


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
