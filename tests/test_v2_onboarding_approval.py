"""Version 2 email verification, consent, and independent production approval."""

import json
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from app.core.config import settings
from app.core.security import decrypt_sensitive_value
from app.models import (
    AuditLog,
    DarajaCredential,
    EmailOutbox,
    EmailVerificationToken,
    Payment,
    User,
    WebhookEndpoint,
)

BASE = "/api/v1"


def _verification_token(db, user_id: str) -> str:
    outbox = (
        db.query(EmailOutbox)
        .filter(EmailOutbox.user_id == user_id, EmailOutbox.template == "email_verification")
        .order_by(EmailOutbox.created_at.desc())
        .first()
    )
    payload = json.loads(decrypt_sensitive_value(outbox.payload_encrypted))
    return parse_qs(urlsplit(payload["url"]).query)["token"][0]


def _register(client, *, email: str, organization_name: str) -> dict:
    response = client.post(
        f"{BASE}/auth/register",
        json={
            "organization_name": organization_name,
            "contact_email": email,
            "full_name": f"{organization_name} Owner",
            "password": "version-two-secure-password",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_registration_queues_hashed_email_verification_and_confirm_is_single_use(db, client):
    tokens = _register(
        client, email="verification@example.co.ke", organization_name="Verification Merchant"
    )
    assert tokens["email_verification_required"] is True
    user = db.query(User).filter_by(email="verification@example.co.ke").one()
    assert user.email_verified_at is None
    record = db.query(EmailVerificationToken).filter_by(user_id=user.id).one()
    raw_token = _verification_token(db, user.id)
    assert raw_token not in record.token_hash
    assert raw_token not in db.query(EmailOutbox).filter_by(user_id=user.id).one().payload_encrypted

    confirmed = client.post(f"{BASE}/auth/email-verification/confirm", json={"token": raw_token})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["verified"] is True
    db.refresh(user)
    assert user.email_verified_at is not None
    assert db.query(AuditLog).filter_by(entity_id=user.id, action="email_verified").count() == 1
    replay = client.post(f"{BASE}/auth/email-verification/confirm", json={"token": raw_token})
    assert replay.status_code == 400


def test_production_activation_requires_consent_submission_and_independent_platform_approval(
    db, client, auth_headers
):
    owner = db.query(User).filter_by(email="owner@acme.co.ke").one()
    owner_token = _verification_token(db, owner.id)
    assert (
        client.post(
            f"{BASE}/auth/email-verification/confirm", json={"token": owner_token}
        ).status_code
        == 200
    )
    consent = client.post(
        f"{BASE}/organization/consents",
        headers=auth_headers,
        json={
            "accept_terms": True,
            "accept_privacy": True,
            "terms_version": settings.TERMS_VERSION,
            "privacy_version": settings.PRIVACY_VERSION,
        },
    )
    assert consent.status_code == 200, consent.text

    merchant = client.post(
        f"{BASE}/merchants",
        headers=auth_headers,
        json={
            "merchant_name": "Acme Production PayBill",
            "shortcode": "777888",
            "shortcode_type": "paybill",
            "environment": "production",
        },
    )
    assert merchant.status_code == 201, merchant.text
    merchant_data = merchant.json()
    credentials = client.post(
        f"{BASE}/merchants/{merchant_data['id']}/daraja-credentials",
        headers=auth_headers,
        json={
            "consumer_key": "production-consumer-key",
            "consumer_secret": "production-consumer-secret",
            "passkey": "production-passkey",
            "shortcode": "777888",
            "environment": "production",
        },
    )
    assert credentials.status_code == 201, credentials.text
    with patch(
        "app.router.DarajaClient.get_access_token",
        new=AsyncMock(return_value="production-oauth-token"),
    ):
        tested = client.post(
            f"{BASE}/merchants/{merchant_data['id']}/daraja-credentials/test",
            headers=auth_headers,
        )
    assert tested.status_code == 200, tested.text
    credential = (
        db.query(DarajaCredential)
        .filter_by(merchant_account_id=merchant_data["id"], is_active=True)
        .one()
    )
    verification_payment = Payment(
        organization_id=merchant_data["organization_id"],
        merchant_account_id=merchant_data["id"],
        external_reference="PRODUCTION-KES-1-VERIFICATION",
        customer_phone="254712345678",
        amount="1.00",
        currency="KES",
        description="Production KES 1 verification",
        purpose="merchant_verification",
        status="success",
        checkout_request_id="ws_PRODUCTION_VERIFY",
        mpesa_receipt_number="PRODVERIFY1",
        result_code="0",
        success_source="callback",
        receipt_status="present",
        provider_acceptance_state="accepted",
    )
    db.add(verification_payment)
    db.commit()
    assert verification_payment.created_at >= credential.last_tested_at

    direct_activation = client.patch(
        f"{BASE}/merchants/{merchant_data['id']}",
        headers=auth_headers,
        json={"status": "active"},
    )
    assert direct_activation.status_code == 409
    submitted = client.post(
        f"{BASE}/merchants/{merchant_data['id']}/submit-for-approval",
        headers=auth_headers,
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "pending_approval"

    merchant_admin_attempt = client.post(
        f"{BASE}/admin/merchants/{merchant_data['id']}/approve",
        headers=auth_headers,
        json={"reason": "Merchant owners cannot approve themselves"},
    )
    assert merchant_admin_attempt.status_code == 403

    platform_tokens = _register(
        client,
        email="approver@lynxpay.co.ke",
        organization_name="LynxPay Platform Operations",
    )
    platform_user = db.query(User).filter_by(email="approver@lynxpay.co.ke").one()
    platform_user.is_platform_admin = True
    db.commit()
    platform_headers = {"Authorization": f"Bearer {platform_tokens['access_token']}"}
    queue = client.get(f"{BASE}/admin/merchants/pending-approval", headers=platform_headers)
    assert queue.status_code == 200, queue.text
    assert [row["id"] for row in queue.json()["items"]] == [merchant_data["id"]]

    approved = client.post(
        f"{BASE}/admin/merchants/{merchant_data['id']}/approve",
        headers=platform_headers,
        json={"reason": "Business and M-PESA evidence reviewed by pilot operations"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "active"
    approval_audit = (
        db.query(AuditLog)
        .filter_by(merchant_account_id=merchant_data["id"], action="production_merchant_approved")
        .one()
    )
    assert approval_audit.actor_user_id == platform_user.id
    active = client.get(f"{BASE}/admin/merchants?status=active", headers=platform_headers)
    assert active.status_code == 200, active.text
    assert [row["id"] for row in active.json()["items"]] == [merchant_data["id"]]
    suspended = client.post(
        f"{BASE}/admin/merchants/{merchant_data['id']}/suspend",
        headers=platform_headers,
        json={"reason": "Pilot operations suspension drill completed safely"},
    )
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["status"] == "suspended"
    assert (
        db.query(AuditLog)
        .filter_by(merchant_account_id=merchant_data["id"], action="merchant_suspended")
        .count()
        == 1
    )


def test_webhook_read_scope_management_test_delivery_rotation_and_archive(db, client, auth_headers):
    merchant = client.post(
        f"{BASE}/merchants",
        headers=auth_headers,
        json={
            "merchant_name": "Webhook Sandbox PayBill",
            "shortcode": "881122",
            "shortcode_type": "paybill",
            "environment": "sandbox",
        },
    ).json()
    created = client.post(
        f"{BASE}/webhooks/endpoints",
        headers=auth_headers,
        json={
            "merchant_id": merchant["id"],
            "url": "https://merchant.example.co.ke/lynxpay/webhook",
            "event_types": ["payment.success", "payment.failed"],
        },
    )
    assert created.status_code == 201, created.text
    endpoint_id = created.json()["id"]
    first_secret = created.json()["signing_secret"]
    endpoint = db.query(WebhookEndpoint).filter_by(id=endpoint_id).one()
    assert first_secret not in endpoint.secret_encrypted

    read_key = client.post(
        f"{BASE}/api-keys",
        headers=auth_headers,
        json={
            "name": "Webhook observer",
            "merchant_id": merchant["id"],
            "environment": "sandbox",
            "scopes": ["webhooks:read"],
        },
    )
    assert read_key.status_code == 201, read_key.text
    read_headers = {"X-API-Key": read_key.json()["api_key"]}
    listing = client.get(f"{BASE}/webhooks/endpoints", headers=read_headers)
    assert listing.status_code == 200, listing.text
    assert listing.json()["items"][0]["id"] == endpoint_id
    assert "signing_secret" not in listing.json()["items"][0]
    assert (
        client.post(
            f"{BASE}/webhooks/endpoints/{endpoint_id}/test", headers=read_headers
        ).status_code
        == 403
    )

    tested = client.post(f"{BASE}/webhooks/endpoints/{endpoint_id}/test", headers=auth_headers)
    assert tested.status_code == 201, tested.text
    delivery = client.get(f"{BASE}/webhooks/deliveries/{tested.json()['id']}", headers=read_headers)
    assert delivery.status_code == 200, delivery.text
    assert delivery.json()["event_type"] == "webhook.test"
    assert delivery.json()["delivery_attempts"] == []

    rotated = client.post(
        f"{BASE}/webhooks/endpoints/{endpoint_id}/rotate-secret", headers=auth_headers
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["signing_secret"] != first_secret
    db.refresh(endpoint)
    assert rotated.json()["signing_secret"] not in endpoint.secret_encrypted

    archived = client.delete(f"{BASE}/webhooks/endpoints/{endpoint_id}", headers=auth_headers)
    assert archived.status_code == 204
    assert client.get(f"{BASE}/webhooks/endpoints", headers=read_headers).json()["items"] == []
