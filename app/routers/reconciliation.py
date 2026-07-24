"""LynxPay domain HTTP routes."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.deps import (
    Principal,
    require_scope,
    scoped_merchant,
)
from app.models import (
    AuditLog,
    Payment,
    PaymentStatusCheck,
)
from app.reconciliation import reconcile_payment
from app.routers.payments import _payments_query
from app.service import (
    payment_payload,
)

router = APIRouter(tags=["LynxPay"])


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
