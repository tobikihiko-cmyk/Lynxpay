"""LynxPay domain HTTP routes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.deps import (
    Principal,
    require_scope,
    scoped_merchant,
)
from app.models import (
    CatalogItem,
    InvoiceLineItem,
    MerchantAccount,
)
from app.schemas import (
    CatalogItemCreate,
    CatalogItemPatch,
)
from app.service import (
    audit,
)

router = APIRouter(tags=["LynxPay"])


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
