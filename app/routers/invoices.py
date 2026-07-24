"""LynxPay domain HTTP routes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.database import set_resource_context, set_tenant_context
from app.deps import (
    Principal,
    require_scope,
    scoped_merchant,
)
from app.models import (
    CatalogItem,
    Invoice,
    InvoiceLineItem,
    MerchantAccount,
    Organization,
    Payment,
    PaymentAttempt,
)
from app.observability import (
    PAYMENTS_CREATED,
)
from app.routers.payments import _submit_stk_attempt
from app.schemas import (
    InvoiceCreate,
    InvoicePayRequest,
)
from app.service import (
    active_credential,
    audit,
    ledger,
    request_fingerprint,
    transition_and_record,
    utcnow,
)

router = APIRouter(tags=["LynxPay"])


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
    set_resource_context(db, "public_invoice_id", public_id)
    invoice = db.query(Invoice).filter(Invoice.public_id == public_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    set_tenant_context(db, invoice.organization_id)
    return _public_invoice_view(invoice)


@router.post("/public/invoices/{public_id}/pay", status_code=201)
async def pay_public_invoice(
    public_id: str,
    payload: InvoicePayRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    set_resource_context(db, "public_invoice_id", public_id)
    invoice = db.query(Invoice).filter(Invoice.public_id == public_id).with_for_update().first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    set_tenant_context(db, invoice.organization_id)
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
        correlation_id=request.state.request_id,
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
