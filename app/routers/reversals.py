"""Controlled M-PESA reversal routes and provider callbacks."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.callback_security import callback_allowed, read_callback_body
from app.core.config import settings
from app.core.deps import get_client_ip, get_db
from app.core.security import hash_opaque_token
from app.daraja import DarajaClient, redact_reversal_payload
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
from app.reversal_controls import (
    bind_reversal_to_payment,
    merge_reversal_response_evidence,
    reversal_binding_error,
)
from app.schemas import ReversalApproval, ReversalRequestCreate
from app.service import (
    active_credential,
    audit,
    decrypted_reversal_credentials,
    decrypted_secrets,
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
    "timeout",
    "unknown",
}
TERMINAL_REVERSAL_STATUSES = {"succeeded", "failed", "cancelled"}
STATUS_QUERYABLE_REVERSAL_STATUSES = {"submitted", "timeout", "unknown"}
STATUS_QUERY_COOLDOWN_SECONDS = 30
STATUS_QUERY_MAX_ATTEMPTS = 10


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
    if Decimal(payment.amount) != Decimal(payment.amount).to_integral_value():
        raise HTTPException(
            status_code=409,
            detail="Daraja reversals require a whole KES amount",
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
    bind_reversal_to_payment(reversal, payment)
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
    payment_query = db.query(Payment).filter(
        Payment.id == reversal.payment_id,
        Payment.organization_id == reversal.organization_id,
        Payment.merchant_account_id == reversal.merchant_account_id,
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        payment_query = payment_query.with_for_update()
    payment = payment_query.first()
    binding_error = reversal_binding_error(reversal, payment)
    if binding_error:
        audit(
            db,
            organization_id=reversal.organization_id,
            merchant_id=reversal.merchant_account_id,
            action="reversal_approval_conflict",
            entity_type="reversal_request",
            entity_id=reversal.id,
            principal=principal,
            request=request,
            metadata={"reason": binding_error},
        )
        db.commit()
        raise HTTPException(
            status_code=409,
            detail=f"The payment is no longer eligible for reversal: {binding_error}",
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
    merge_reversal_response_evidence(
        reversal,
        "approval",
        {
            "approved_by_user_id": principal.user_id,
            "approved_at": reversal.approved_at.isoformat(),
            "note": payload.note,
        },
    )
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


@router.post("/reversals/{reversal_id}/status-query", status_code=202)
async def query_reversal_status(
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
    if reversal.status not in STATUS_QUERYABLE_REVERSAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Reversal status cannot be queried from {reversal.status}",
        )

    payment_query = db.query(Payment).filter(
        Payment.id == reversal.payment_id,
        Payment.organization_id == reversal.organization_id,
        Payment.merchant_account_id == reversal.merchant_account_id,
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        payment_query = payment_query.with_for_update()
    payment = payment_query.first()
    binding_error = reversal_binding_error(
        reversal,
        payment,
        eligible_statuses={"success", "reversed"},
    )
    if binding_error:
        raise HTTPException(status_code=409, detail=f"Reversal binding conflict: {binding_error}")

    merchant = (
        db.query(MerchantAccount).filter(MerchantAccount.id == reversal.merchant_account_id).first()
    )
    if not merchant or not payment:
        raise HTTPException(status_code=409, detail="Reversal merchant or payment is unavailable")
    credential = active_credential(db, merchant)
    secrets = decrypted_secrets(credential)
    initiator_name, security_credential = decrypted_reversal_credentials(credential)

    response_evidence = dict(reversal.response_payload or {})
    status_queries = list(response_evidence.get("status_queries") or [])
    if len(status_queries) >= STATUS_QUERY_MAX_ATTEMPTS:
        raise HTTPException(status_code=409, detail="Maximum reversal status queries reached")
    if status_queries:
        requested_at = status_queries[-1].get("requested_at")
        try:
            previous = datetime.fromisoformat(str(requested_at))
        except (TypeError, ValueError):
            previous = None
        if previous and (utcnow() - previous).total_seconds() < STATUS_QUERY_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"Wait {STATUS_QUERY_COOLDOWN_SECONDS} seconds between status queries",
            )

    attempt = {
        "requested_at": utcnow().isoformat(),
        "requested_by_user_id": principal.user_id,
        "state": "submitting",
    }
    status_queries.append(attempt)
    merge_reversal_response_evidence(reversal, "status_queries", status_queries)
    audit(
        db,
        organization_id=reversal.organization_id,
        merchant_id=reversal.merchant_account_id,
        action="reversal_status_query_requested",
        entity_type="reversal_request",
        entity_id=reversal.id,
        principal=principal,
        request=request,
        metadata={"attempt": len(status_queries), "payment_id": payment.id},
    )
    db.commit()

    base_url = settings.public_url
    result_url = f"{base_url}/api/v1/callbacks/mpesa/reversals/{merchant.id}/status-result"
    timeout_url = f"{base_url}/api/v1/callbacks/mpesa/reversals/{merchant.id}/status-timeout"
    try:
        provider_response, sent_payload = await DarajaClient(
            merchant.environment
        ).query_transaction_status(
            secrets=secrets,
            initiator_name=initiator_name,
            security_credential=security_credential,
            shortcode=merchant.shortcode,
            transaction_id=payment.mpesa_receipt_number,
            result_url=result_url,
            timeout_url=timeout_url,
            remarks=f"Reversal status {reversal.id}",
            occasion=f"LynxPay reversal {reversal.id}",
            correlation_id=payment.correlation_id,
        )
    except Exception:
        reversal = (
            _scoped_reversals(db, principal).filter(ReversalRequest.id == reversal_id).first()
        )
        response_evidence = dict(reversal.response_payload or {})
        status_queries = list(response_evidence.get("status_queries") or [])
        if status_queries:
            status_queries[-1] = {**status_queries[-1], "state": "submission_failed"}
        merge_reversal_response_evidence(reversal, "status_queries", status_queries)
        audit(
            db,
            organization_id=reversal.organization_id,
            merchant_id=reversal.merchant_account_id,
            action="reversal_status_query_submission_failed",
            entity_type="reversal_request",
            entity_id=reversal.id,
            principal=principal,
            request=request,
        )
        db.commit()
        raise HTTPException(
            status_code=502,
            detail="Daraja reversal status query could not be submitted",
        ) from None

    reversal = _scoped_reversals(db, principal).filter(ReversalRequest.id == reversal_id).first()
    response_evidence = dict(reversal.response_payload or {})
    status_queries = list(response_evidence.get("status_queries") or [])
    status_queries[-1] = {
        **status_queries[-1],
        "state": "accepted",
        "provider_response": provider_response,
        "request": redact_reversal_payload(sent_payload),
    }
    merge_reversal_response_evidence(reversal, "status_queries", status_queries)
    audit(
        db,
        organization_id=reversal.organization_id,
        merchant_id=reversal.merchant_account_id,
        action="reversal_status_query_accepted",
        entity_type="reversal_request",
        entity_id=reversal.id,
        principal=principal,
        request=request,
        metadata={"response_code": provider_response.get("ResponseCode")},
    )
    db.commit()
    return {
        "reversal_id": reversal.id,
        "status": reversal.status,
        "query_state": "accepted",
    }


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
        "transaction_status": parameters.get("TransactionStatus")
        or parameters.get("Status")
        or result.get("TransactionStatus"),
        "parameters": parameters,
    }


@router.post("/callbacks/mpesa/reversals/{merchant_id}/{callback_type}")
async def receive_reversal_callback(
    merchant_id: str,
    callback_type: str,
    request: Request,
    db: Session = Depends(get_db),
):
    if callback_type not in {"result", "timeout", "status-result", "status-timeout"}:
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
    has_provider_ids = bool(fields["originator_conversation_id"] or fields["conversation_id"])
    if not has_provider_ids and not (
        callback_type.startswith("status-") and fields["transaction_id"]
    ):
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
    reversal = None
    if provider_ids:
        query = db.query(ReversalRequest).filter(
            ReversalRequest.merchant_account_id == merchant.id,
            or_(*provider_ids),
        )
        if db.bind and db.bind.dialect.name == "postgresql":
            query = query.with_for_update()
        reversal = query.first()
    if not reversal and callback_type.startswith("status-") and fields["transaction_id"]:
        query = (
            db.query(ReversalRequest)
            .join(Payment, Payment.id == ReversalRequest.payment_id)
            .filter(
                ReversalRequest.merchant_account_id == merchant.id,
                ReversalRequest.status.in_(STATUS_QUERYABLE_REVERSAL_STATUSES),
                Payment.mpesa_receipt_number == str(fields["transaction_id"]),
            )
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

    if reversal.status in TERMINAL_REVERSAL_STATUSES:
        callback.processing_status = (
            "duplicate" if reversal.status == "succeeded" else "terminal_state_conflict"
        )
        callback.processed_at = utcnow()
        audit(
            db,
            organization_id=reversal.organization_id,
            merchant_id=reversal.merchant_account_id,
            action="reversal_callback_terminal_conflict",
            entity_type="reversal_request",
            entity_id=reversal.id,
            request=request,
            metadata={
                "callback_id": callback.id,
                "terminal_status": reversal.status,
                "callback_type": callback_type,
            },
        )
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
    elif callback_type == "status-timeout":
        reversal.status = "unknown"
        reversal.response_description = (
            fields["result_description"] or "Daraja transaction-status query timed out"
        )
        reversal.completed_at = now
        callback.processing_status = "status_query_timeout"
    elif callback_type == "status-result" and fields["result_code"] != "0":
        reversal.status = "unknown"
        reversal.response_code = fields["result_code"]
        reversal.response_description = (
            fields["result_description"] or "Daraja could not determine reversal status"
        )
        reversal.completed_at = now
        callback.processing_status = "status_query_inconclusive"
    else:
        transaction_status = str(fields["transaction_status"] or "").strip().lower()
        status_confirms_reversal = transaction_status in {
            "reversed",
            "transaction reversed",
            "fully reversed",
        }
        provider_confirms_reversal = (
            callback_type == "result" and fields["result_code"] == "0"
        ) or (
            callback_type == "status-result"
            and fields["result_code"] == "0"
            and status_confirms_reversal
        )
        if not provider_confirms_reversal:
            reversal.status = "failed" if callback_type == "result" else "unknown"
            reversal.response_code = fields["result_code"]
            reversal.response_description = fields["result_description"] or (
                f"Daraja reports transaction status {transaction_status}"
                if transaction_status
                else "Daraja did not confirm the reversal"
            )
            reversal.completed_at = now
            callback.processing_status = (
                "processed_failure" if callback_type == "result" else "status_query_not_reversed"
            )
        else:
            payment_query = db.query(Payment).filter(
                Payment.id == reversal.payment_id,
                Payment.organization_id == reversal.organization_id,
                Payment.merchant_account_id == reversal.merchant_account_id,
            )
            if db.bind and db.bind.dialect.name == "postgresql":
                payment_query = payment_query.with_for_update()
            payment = payment_query.first()
            binding_error = reversal_binding_error(
                reversal,
                payment,
                eligible_statuses={"success", "reversed"},
            )
            if binding_error:
                reversal.status = "unknown"
                reversal.response_description = (
                    f"Provider confirmed reversal but payment binding failed: {binding_error}"
                )
                reversal.completed_at = now
                callback.processing_status = "payment_binding_conflict"
            else:
                if payment.status == "success":
                    transition_and_record(
                        db,
                        payment=payment,
                        target="reversed",
                        event_type="payment.reversed",
                        request=request,
                        details={
                            "reversal_id": reversal.id,
                            "conversation_id": fields["conversation_id"],
                            "confirmation_source": callback_type,
                        },
                    )
                    queue_webhooks(db, payment, "payment.reversed")
                reversal.status = "succeeded"
                if callback_type == "result" and fields["transaction_id"]:
                    reversal.provider_transaction_id = str(fields["transaction_id"])
                reversal.response_code = fields["result_code"]
                reversal.response_description = fields["result_description"]
                reversal.completed_at = now
                callback.processing_status = "processed_success"

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
