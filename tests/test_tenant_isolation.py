"""Cross-tenant authorization tests for identity and merchant resources."""

from app.models import (
    ApiKey,
    AuthSession,
    CatalogItem,
    Invoice,
    MerchantAccount,
    TeamInvitation,
    User,
    WebhookEndpoint,
)

BASE = "/api/v1"


def _register(client, *, slug: str) -> tuple[dict, dict]:
    response = client.post(
        f"{BASE}/auth/register",
        json={
            "organization_name": f"{slug.title()} Limited",
            "contact_email": f"owner@{slug}.example.co.ke",
            "full_name": f"{slug.title()} Owner",
            "password": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body, {"Authorization": f"Bearer {body['access_token']}"}


def _merchant(client, headers: dict, *, slug: str, shortcode: str) -> dict:
    response = client.post(
        f"{BASE}/merchants",
        headers=headers,
        json={
            "merchant_name": f"{slug.title()} Merchant",
            "shortcode": shortcode,
            "shortcode_type": "paybill",
            "environment": "sandbox",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_tenant_b_cannot_read_or_mutate_tenant_a_resources(db, client):
    tenant_a, headers_a = _register(client, slug="tenant-a")
    tenant_b, headers_b = _register(client, slug="tenant-b")
    merchant_a = _merchant(client, headers_a, slug="tenant-a", shortcode="501001")
    merchant_b = _merchant(client, headers_b, slug="tenant-b", shortcode="501002")

    merchant_record = db.query(MerchantAccount).filter_by(id=merchant_a["id"]).one()
    merchant_record.status = "active"
    db.commit()

    catalog = client.post(
        f"{BASE}/catalog-items",
        headers=headers_a,
        json={
            "merchant_id": merchant_a["id"],
            "item_type": "service",
            "name": "Tenant A consultation",
            "unit_price": "1500",
        },
    )
    assert catalog.status_code == 201, catalog.text
    catalog_id = catalog.json()["id"]

    invoice = client.post(
        f"{BASE}/invoices",
        headers=headers_a,
        json={
            "merchant_id": merchant_a["id"],
            "client_name": "Tenant A client",
            "service_title": "Private advisory",
            "description": "Tenant A confidential invoice",
            "amount": "1500",
        },
    )
    assert invoice.status_code == 201, invoice.text
    invoice_id = invoice.json()["id"]

    api_key = client.post(
        f"{BASE}/api-keys",
        headers=headers_a,
        json={
            "name": "Tenant A integration",
            "merchant_id": merchant_a["id"],
            "environment": "sandbox",
            "scopes": ["payments:read"],
        },
    )
    assert api_key.status_code == 201, api_key.text
    api_key_id = api_key.json()["id"]

    webhook = client.post(
        f"{BASE}/webhooks/endpoints",
        headers=headers_a,
        json={
            "merchant_id": merchant_a["id"],
            "url": "https://tenant-a.example.test/lynxpay",
            "event_types": ["payment.success"],
        },
    )
    assert webhook.status_code == 201, webhook.text
    webhook_id = webhook.json()["id"]

    invitation = client.post(
        f"{BASE}/team/invitations",
        headers=headers_a,
        json={"email": "member@tenant-a.example.co.ke", "role": "operator"},
    )
    assert invitation.status_code == 201, invitation.text
    invitation_id = invitation.json()["id"]

    user_a = db.query(User).filter_by(organization_id=tenant_a["user"]["organization_id"]).one()
    session_a = db.query(AuthSession).filter_by(user_id=user_a.id, status="active").one()

    assert client.get(f"{BASE}/merchants/{merchant_a['id']}", headers=headers_b).status_code == 404
    assert (
        client.patch(
            f"{BASE}/merchants/{merchant_a['id']}",
            headers=headers_b,
            json={"merchant_name": "Compromised"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{BASE}/merchants/{merchant_a['id']}/daraja-credentials",
            headers=headers_b,
            json={
                "consumer_key": "cross-tenant-key",
                "consumer_secret": "cross-tenant-secret",
                "passkey": "cross-tenant-passkey",
                "shortcode": merchant_a["shortcode"],
                "environment": "sandbox",
            },
        ).status_code
        == 404
    )
    assert client.delete(f"{BASE}/api-keys/{api_key_id}", headers=headers_b).status_code == 404
    assert (
        client.patch(
            f"{BASE}/catalog-items/{catalog_id}",
            headers=headers_b,
            json={"name": "Compromised"},
        ).status_code
        == 404
    )
    assert client.get(f"{BASE}/invoices/{invoice_id}", headers=headers_b).status_code == 404
    assert client.post(f"{BASE}/invoices/{invoice_id}/void", headers=headers_b).status_code == 404
    assert (
        client.patch(
            f"{BASE}/webhooks/endpoints/{webhook_id}",
            headers=headers_b,
            json={"status": "disabled"},
        ).status_code
        == 404
    )
    assert (
        client.delete(f"{BASE}/team/invitations/{invitation_id}", headers=headers_b).status_code
        == 404
    )
    assert (
        client.patch(
            f"{BASE}/team/users/{user_a.id}",
            headers=headers_b,
            json={"role": "read_only"},
        ).status_code
        == 404
    )
    assert (
        client.delete(f"{BASE}/auth/sessions/{session_a.id}", headers=headers_b).status_code == 404
    )

    organization_b = client.get(f"{BASE}/organization", headers=headers_b)
    assert organization_b.status_code == 200
    assert organization_b.json()["id"] == tenant_b["user"]["organization_id"]
    assert {
        row["id"] for row in client.get(f"{BASE}/merchants", headers=headers_b).json()["items"]
    } == {merchant_b["id"]}
    assert client.get(f"{BASE}/catalog-items", headers=headers_b).json()["items"] == []
    assert client.get(f"{BASE}/invoices", headers=headers_b).json()["items"] == []
    assert client.get(f"{BASE}/api-keys", headers=headers_b).json()["items"] == []
    assert client.get(f"{BASE}/webhooks/endpoints", headers=headers_b).json()["items"] == []
    assert client.get(f"{BASE}/team/invitations", headers=headers_b).json()["items"] == []
    assert all(
        row.organization_id == tenant_b["user"]["organization_id"]
        for row in db.query(User).filter(
            User.organization_id == tenant_b["user"]["organization_id"]
        )
    )

    assert db.query(ApiKey).filter_by(id=api_key_id, status="active").count() == 1
    assert db.query(CatalogItem).filter_by(id=catalog_id, name="Tenant A consultation").count() == 1
    assert db.query(Invoice).filter_by(id=invoice_id, status="sent").count() == 1
    assert db.query(WebhookEndpoint).filter_by(id=webhook_id, status="active").count() == 1
    assert db.query(TeamInvitation).filter_by(id=invitation_id, status="pending").count() == 1
    assert db.query(AuthSession).filter_by(id=session_a.id, status="active").count() == 1
