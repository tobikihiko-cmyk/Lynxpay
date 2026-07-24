"""LynxPay domain HTTP routes."""

from __future__ import annotations

from datetime import datetime
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_db
from app.core.security import (
    encrypt_sensitive_value,
    encryption_key_version,
)
from app.deps import (
    Principal,
    require_scope,
    scoped_merchant,
)
from app.models import (
    MerchantAccount,
    WebhookDelivery,
    WebhookDeliveryAttempt,
    WebhookEndpoint,
)
from app.schemas import (
    WebhookEndpointCreate,
    WebhookEndpointUpdate,
)
from app.service import (
    audit,
    utcnow,
)
from app.webhooks import UnsafeWebhookUrlError, validate_webhook_url

router = APIRouter(tags=["LynxPay"])


def _ensure_https(url: str, environment: str) -> None:
    if environment == "production" and not url.lower().startswith("https://"):
        raise HTTPException(
            status_code=422, detail="Production callback and webhook URLs must use HTTPS"
        )


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
