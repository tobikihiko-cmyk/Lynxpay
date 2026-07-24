"""Controlled M-PESA reversal routes and provider callbacks."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.callback_security import callback_allowed, read_callback_body
from app.core.deps import get_client_ip, get_db
from app.core.security import hash_opaque_token
from app.database import set_resource_context, set_tenant_context
from app.deps import Principal, require_control_admin, require_scope
from app.models import (
    MerchantAccount,
    Payment,
    ReversalCallback,
    ReversalRequest,
)
from app.observability import (
    MERCHANT_PAYMENT_OUTCOMES,
    MPESA_RESULT_CODES,
    PAYMENT_OUTCOMES,
)
from app.schemas import ReversalApproval, ReversalRequestCreate
from app.service import (
    active_credential,
    audit,
    decrypted_reversal_credentials,
    queue_webhooks,
    request_fingerprint,
    transition_and_record,
    utcnow,
)

router = APIRouter(tags=["Reversals"])

ACTIVE_REVERSAL_STATUSES = {
    "pending_approval",
    "approved",
    "submitting",
    "submitted",
    "unknown",
}


def reversal_view(reversal: ReversalRequest) -> dict:
    return {
        "id": reversal.id,
        "organization_id": reversal.organization_id,
        "merchant_id": reversal.merchant_account_id,
        "payment_id": reversal.payment_id,
        "amount": str(reversal.amount),
        "currency": reversal.currency,
        "reason": reversal.reason,
        "status": reversal.status,
        "requested_by_user_id": reversal.requested_by_user_id,
        "approved_by_user_id": reversal.approved_by_user_id,
        "originator_conversation_id": reversal.originator_conversation_id,
        "conversation_id": reversal.conversation_id,
        "provider_transaction_id": reversal.provider_transaction_id,
        "response_code": reversal.response_code,
        "response_description": reversal.response_description,
        "approved_at": reversal.approved_at.isoformat() if reversal.approved_at else None,
        "submitted_at": reversal.submitted_at.isoformat() if reversal.submitted_at else None,
        "completed_at": reversal.completed_at.isoformat() if reversal.completed_at else None,
        "created_at": reversal.created_at.isoformat() if reversal.created_at else None,
        "updated_at": reversal.updated_at.isoformat() if reversal.updated_at else None,
    }


def _scoped_reversals(db: Session, principal: Principal):
    query = db.query(ReversalRequest).filter(
        ReversalRequest.organization_id == principal.organization_id
    )
    if principal.merchant_id:
        query = query.filter(ReversalRequest.merchant_account_id == principal.merchant_id)
    return query


@router.post("/payments/{payment_id}/reversals", status_code=201)
def request_reversal(
    payment_id: str,
    payload: ReversalRequestCreate,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    payment_query = db.query(Payment).filter(
        Payment.id == payment_id,
        Payment.organization_id == principal.organization_id,
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        payment_query = payment_query.with_for_update()
    payment = payment_query.first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.purpose != "payment":
        raise HTTPException(
            status_code=409, detail="Merchant verification payments cannot be reversed"
        )
    if payment.status != "success" or not payment.mpesa_receipt_number:
        raise HTTPException(
            status_code=409,
            detail="Only successful payments with M-PESA receipt evidence can be reversed",
        )

    digest = hash_opaque_token(idempotency_key)
    fingerprint = request_fingerprint(
        {
            "payment_id": payment.id,
            "amount": str(payment.amount),
            "reason": payload.reason,
        }
    )
    existing = (
        db.query(ReversalRequest)
        .filter(
            ReversalRequest.merchant_account_id == payment.merchant_account_id,
            ReversalRequest.idempotency_key == digest,
        )
        .first()
    )
    if existing:
        if existing.idempotency_request_hash != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="Idempotency key was reused with different reversal data",
            )
        result = reversal_view(existing)
        result["idempotent_replay"] = True
        return result
    active = (
        db.query(ReversalRequest)
        .filter(
            ReversalRequest.payment_id == payment.id,
            ReversalRequest.status.in_(ACTIVE_REVERSAL_STATUSES),
        )
        .first()
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"Payment already has an active reversal request ({active.id})",
        )

    reversal = ReversalRequest(
        organization_id=payment.organization_id,
        merchant_account_id=payment.merchant_account_id,
        payment_id=payment.id,
        idempotency_key=digest,
        idempotency_request_hash=fingerprint,
        amount=payment.amount,
        currency=payment.currency,
        reason=payload.reason,
        status="pending_approval",
        requested_by_user_id=principal.user_id,
    )
    db.add(reversal)
    db.flush()
    audit(
        db,
        organization_id=payment.organization_id,
        merchant_id=payment.merchant_account_id,
        action="reversal_requested",
        entity_type="reversal_request",
        entity_id=reversal.id,
        principal=principal,
        request=request,
        metadata={"payment_id": payment.id, "amount": str(payment.amount)},
    )
    db.commit()
    db.refresh(reversal)
    result = reversal_view(reversal)
    result["idempotent_replay"] = False
    return result


@router.get("/reversals")
def list_reversals(
    payment_id: str | None = None,
    merchant_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:read")),
):
    query = _scoped_reversals(db, principal)
    if payment_id:
        query = query.filter(ReversalRequest.payment_id == payment_id)
    if merchant_id:
        query = query.filter(ReversalRequest.merchant_account_id == merchant_id)
    if status:
        query = query.filter(ReversalRequest.status == status)
    rows = query.order_by(ReversalRequest.created_at.desc()).limit(min(max(limit, 1), 500)).all()
    return {"items": [reversal_view(row) for row in rows]}


@router.get("/reversals/{reversal_id}")
def get_reversal(
    reversal_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("payments:read")),
):
    reversal = _scoped_reversals(db, principal).filter(ReversalRequest.id == reversal_id).first()
    if not reversal:
        raise HTTPException(status_code=404, detail="Reversal request not found")
    return reversal_view(reversal)


@router.post("/reversals/{reversal_id}/approve")
def approve_reversal(
    reversal_id: str,
    payload: ReversalApproval,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    query = _scoped_reversals(db, principal).filter(ReversalRequest.id == reversal_id)
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    reversal = query.first()
    if not reversal:
        raise HTTPException(status_code=404, detail="Reversal request not found")
    if reversal.status != "pending_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Reversal cannot be approved from {reversal.status}",
        )
    if not principal.user_id or principal.user_id == reversal.requested_by_user_id:
        raise HTTPException(
            status_code=409,
            detail="A different owner or administrator must approve this reversal",
        )
    payment = db.query(Payment).filter(Payment.id == reversal.payment_id).first()
    if not payment or payment.status != "success":
        raise HTTPException(
            status_code=409, detail="The payment is no longer eligible for reversal"
        )
    merchant = (
        db.query(MerchantAccount).filter(MerchantAccount.id == reversal.merchant_account_id).first()
    )
    if not merchant:
        raise HTTPException(status_code=409, detail="Merchant account is unavailable")
    credential = active_credential(db, merchant)
    decrypted_reversal_credentials(credential)

    reversal.status = "approved"
    reversal.approved_by_user_id = principal.user_id
    reversal.approved_at = utcnow()
    audit(
        db,
        organization_id=reversal.organization_id,
        merchant_id=reversal.merchant_account_id,
        action="reversal_approved",
        entity_type="reversal_request",
        entity_id=reversal.id,
        principal=principal,
        request=request,
        metadata={"payment_id": reversal.payment_id, "note": payload.note},
    )
    db.commit()
    db.refresh(reversal)
    return reversal_view(reversal)


@router.post("/reversals/{reversal_id}/cancel")
def cancel_reversal(
    reversal_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    query = _scoped_reversals(db, principal).filter(ReversalRequest.id == reversal_id)
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    reversal = query.first()
    if not reversal:
        raise HTTPException(status_code=404, detail="Reversal request not found")
    if reversal.status not in {"pending_approval", "approved"}:
        raise HTTPException(
            status_code=409,
            detail=f"Reversal cannot be cancelled from {reversal.status}",
        )
    reversal.status = "cancelled"
    reversal.completed_at = utcnow()
    audit(
        db,
        organization_id=reversal.organization_id,
        merchant_id=reversal.merchant_account_id,
        action="reversal_cancelled",
        entity_type="reversal_request",
        entity_id=reversal.id,
        principal=principal,
        request=request,
    )
    db.commit()
    db.refresh(reversal)
    return reversal_view(reversal)


def _result_fields(payload: dict) -> dict:
    result = payload.get("Result")
    if not isinstance(result, dict):
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    parameters: dict[str, object] = {}
    container = result.get("ResultParameters")
    if isinstance(container, dict):
        rows = container.get("ResultParameter")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get("Key") is not None:
                    parameters[str(row["Key"])] = row.get("Value")
    code = result.get("ResultCode")
    return {
        "originator_conversation_id": result.get("OriginatorConversationID"),
        "conversation_id": result.get("ConversationID"),
        "result_code": str(code) if code is not None else None,
        "result_description": result.get("ResultDesc"),
        "transaction_id": result.get("TransactionID")
        or parameters.get("TransactionID")
        or parameters.get("ReceiptNo"),
    }


@router.post("/callbacks/mpesa/reversals/{merchant_id}/{callback_type}")
async def receive_reversal_callback(
    merchant_id: str,
    callback_type: str,
    request: Request,
    db: Session = Depends(get_db),
):
    if callback_type not in {"result", "timeout"}:
        raise HTTPException(status_code=404, detail="Unknown reversal callback")
    set_resource_context(db, "merchant_id", merchant_id)
    merchant = db.query(MerchantAccount).filter(MerchantAccount.id == merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")
    set_tenant_context(db, merchant.organization_id)

    raw, oversized = await read_callback_body(request)
    request.state.lynxpay_raw_callback = raw
    raw_text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw_text) if not oversized else {"_truncated": raw_text}
        if not isinstance(payload, dict):
            payload = {"_value": payload}
    except json.JSONDecodeError:
        payload = {"_unparsed": raw_text}
    fields = _result_fields(payload)
    callback = ReversalCallback(
        organization_id=merchant.organization_id,
        merchant_account_id=merchant.id,
        callback_type=callback_type,
        originator_conversation_id=fields["originator_conversation_id"],
        conversation_id=fields["conversation_id"],
        result_code=fields["result_code"],
        result_description=fields["result_description"],
        transaction_id=str(fields["transaction_id"]) if fields["transaction_id"] else None,
        raw_payload=payload,
        raw_body=raw_text,
        processing_status="received",
        source_ip=get_client_ip(request),
    )
    db.add(callback)
    db.flush()
    audit(
        db,
        organization_id=merchant.organization_id,
        merchant_id=merchant.id,
        action="reversal_callback_received",
        entity_type="reversal_callback",
        entity_id=callback.id,
        request=request,
        metadata={"callback_type": callback_type, "result_code": fields["result_code"]},
    )
    db.commit()
    set_tenant_context(db, merchant.organization_id)

    if oversized:
        callback.processing_status = "oversized"
        callback.processed_at = utcnow()
        db.commit()
        raise HTTPException(status_code=413, detail="M-PESA reversal callback body is too large")
    if not callback_allowed(request):
        callback.processing_status = "source_rejected"
        callback.processed_at = utcnow()
        db.commit()
        return {"ResultCode": 0, "ResultDesc": "Accepted"}
    if not fields["originator_conversation_id"] and not fields["conversation_id"]:
        callback.processing_status = "malformed"
        callback.processed_at = utcnow()
        db.commit()
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    provider_ids = []
    if fields["originator_conversation_id"]:
        provider_ids.append(
            ReversalRequest.originator_conversation_id == fields["originator_conversation_id"]
        )
    if fields["conversation_id"]:
        provider_ids.append(ReversalRequest.conversation_id == fields["conversation_id"])
    query = db.query(ReversalRequest).filter(
        ReversalRequest.merchant_account_id == merchant.id,
        or_(*provider_ids),
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    reversal = query.first()
    if not reversal:
        callback.processing_status = "unmatched"
        callback.processed_at = utcnow()
        db.commit()
        return {"ResultCode": 0, "ResultDesc": "Accepted"}
    callback.reversal_request_id = reversal.id

    if reversal.status == "succeeded":
        callback.processing_status = "duplicate"
        callback.processed_at = utcnow()
        db.commit()
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    now = utcnow()
    if callback_type == "timeout":
        reversal.status = "timeout"
        reversal.response_description = (
            fields["result_description"] or "Daraja reversal queue timed out"
        )
        reversal.completed_at = now
        callback.processing_status = "processed_timeout"
    elif fields["result_code"] == "0":
        payment_query = db.query(Payment).filter(Payment.id == reversal.payment_id)
        if db.bind and db.bind.dialect.name == "postgresql":
            payment_query = payment_query.with_for_update()
        payment = payment_query.first()
        if payment and payment.status == "success":
            transition_and_record(
                db,
                payment=payment,
                target="reversed",
                event_type="payment.reversed",
                request=request,
                details={
                    "reversal_id": reversal.id,
                    "conversation_id": fields["conversation_id"],
                },
            )
            queue_webhooks(db, payment, "payment.reversed")
        elif not payment or payment.status != "reversed":
            reversal.status = "unknown"
            reversal.response_description = (
                "Provider confirmed reversal but payment state requires manual review"
            )
            reversal.completed_at = now
            callback.processing_status = "payment_state_conflict"
            callback.processed_at = now
            db.commit()
            return {"ResultCode": 0, "ResultDesc": "Accepted"}
        reversal.status = "succeeded"
        reversal.provider_transaction_id = (
            str(fields["transaction_id"]) if fields["transaction_id"] else None
        )
        reversal.response_code = fields["result_code"]
        reversal.response_description = fields["result_description"]
        reversal.completed_at = now
        callback.processing_status = "processed_success"
    else:
        reversal.status = "failed"
        reversal.response_code = fields["result_code"]
        reversal.response_description = (
            fields["result_description"] or "Daraja rejected the reversal"
        )
        reversal.completed_at = now
        callback.processing_status = "processed_failure"

    callback.processed_at = now
    audit(
        db,
        organization_id=reversal.organization_id,
        merchant_id=reversal.merchant_account_id,
        action=f"reversal_{reversal.status}",
        entity_type="reversal_request",
        entity_id=reversal.id,
        request=request,
        metadata={
            "callback_id": callback.id,
            "result_code": fields["result_code"],
            "conversation_id": fields["conversation_id"],
        },
    )
    db.commit()
    MPESA_RESULT_CODES.labels(f"reversal_{callback_type}", fields["result_code"] or "missing").inc()
    if reversal.status == "succeeded":
        PAYMENT_OUTCOMES.labels("reversed", "reversal_callback").inc()
        MERCHANT_PAYMENT_OUTCOMES.labels(
            reversal.merchant_account_id, "reversed", "reversal_callback"
        ).inc()
    return {"ResultCode": 0, "ResultDesc": "Accepted"}
