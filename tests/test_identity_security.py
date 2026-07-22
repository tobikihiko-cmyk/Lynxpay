"""Password reset, MFA, refresh rotation, email outbox, and revocation tests."""

import json
import time
from urllib.parse import parse_qs, urlsplit

from app.core.config import settings
from app.core.security import decrypt_sensitive_value, encryption_key_version, totp_code
from app.database import SessionLocal
from app.models import AuditLog, AuthSession, EmailOutbox, MfaTotpCredential, User
from app.rotate_encryption import rotate

BASE = "/api/v1"


def _register(client, email="security-owner@example.co.ke"):
    response = client.post(
        f"{BASE}/auth/register",
        json={
            "organization_name": "Security Test Org",
            "contact_email": email,
            "full_name": "Security Owner",
            "password": "initial-secure-password",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_refresh_rotation_and_reuse_revokes_entire_family(db, client):
    initial = _register(client)
    rotated = client.post(f"{BASE}/auth/refresh", json={"refresh_token": initial["refresh_token"]})
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["refresh_token"] != initial["refresh_token"]
    assert db.query(AuthSession).filter_by(status="rotated").count() == 1

    reuse = client.post(f"{BASE}/auth/refresh", json={"refresh_token": initial["refresh_token"]})
    assert reuse.status_code == 401
    assert "reuse detected" in reuse.json()["detail"]
    replacement_access = rotated.json()["access_token"]
    assert (
        client.get(
            f"{BASE}/auth/me", headers={"Authorization": f"Bearer {replacement_access}"}
        ).status_code
        == 401
    )


def test_session_revocation_invalidates_access_token(client):
    tokens = _register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    sessions = client.get(f"{BASE}/auth/sessions", headers=headers)
    assert sessions.status_code == 200
    session_id = sessions.json()["items"][0]["id"]
    revoked = client.delete(f"{BASE}/auth/sessions/{session_id}", headers=headers)
    assert revoked.status_code == 204
    assert client.get(f"{BASE}/auth/me", headers=headers).status_code == 401


def test_totp_mfa_and_recovery_code_login(db, client):
    tokens = _register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    setup = client.post(f"{BASE}/auth/mfa/setup", headers=headers)
    assert setup.status_code == 201, setup.text
    secret = setup.json()["secret"]
    recovery_code = setup.json()["recovery_codes"][0]
    credential = db.query(MfaTotpCredential).one()
    assert secret not in credential.secret_encrypted

    confirmed = client.post(
        f"{BASE}/auth/mfa/confirm", headers=headers, json={"code": totp_code(secret)}
    )
    assert confirmed.status_code == 200
    replayed_totp = client.post(
        f"{BASE}/auth/login",
        json={
            "email": "security-owner@example.co.ke",
            "password": "initial-secure-password",
            "mfa_code": totp_code(secret),
        },
    )
    assert replayed_totp.status_code == 401
    missing = client.post(
        f"{BASE}/auth/login",
        json={"email": "security-owner@example.co.ke", "password": "initial-secure-password"},
    )
    assert missing.status_code == 401
    login = client.post(
        f"{BASE}/auth/login",
        json={
            "email": "security-owner@example.co.ke",
            "password": "initial-secure-password",
            "mfa_code": totp_code(secret, at_time=int(time.time()) + 30),
        },
    )
    assert login.status_code == 200, login.text
    recovery_login = client.post(
        f"{BASE}/auth/login",
        json={
            "email": "security-owner@example.co.ke",
            "password": "initial-secure-password",
            "mfa_code": recovery_code,
        },
    )
    assert recovery_login.status_code == 200
    reused = client.post(
        f"{BASE}/auth/login",
        json={
            "email": "security-owner@example.co.ke",
            "password": "initial-secure-password",
            "mfa_code": recovery_code,
        },
    )
    assert reused.status_code == 401


def test_password_reset_token_is_hashed_email_payload_encrypted_and_sessions_revoked(db, client):
    tokens = _register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    requested = client.post(
        f"{BASE}/auth/password-reset/request", json={"email": "security-owner@example.co.ke"}
    )
    assert requested.status_code == 202
    outbox = db.query(EmailOutbox).filter_by(template="password_reset").one()
    assert outbox.payload_encrypted.startswith("env1::")
    payload = json.loads(decrypt_sensitive_value(outbox.payload_encrypted))
    token = parse_qs(urlsplit(payload["url"]).query)["token"][0]
    assert token not in outbox.payload_encrypted

    confirmed = client.post(
        f"{BASE}/auth/password-reset/confirm",
        json={"token": token, "new_password": "replacement-secure-password"},
    )
    assert confirmed.status_code == 200
    assert client.get(f"{BASE}/auth/me", headers=headers).status_code == 401
    assert (
        client.post(
            f"{BASE}/auth/login",
            json={
                "email": "security-owner@example.co.ke",
                "password": "replacement-secure-password",
            },
        ).status_code
        == 200
    )


def test_unknown_password_reset_is_indistinguishable_and_creates_no_email(db, client):
    response = client.post(
        f"{BASE}/auth/password-reset/request", json={"email": "missing@example.co.ke"}
    )
    assert response.status_code == 202
    assert db.query(EmailOutbox).count() == 0


def test_new_password_reset_revokes_all_older_reset_tokens(db, client):
    _register(client)
    for _ in range(2):
        assert (
            client.post(
                f"{BASE}/auth/password-reset/request",
                json={"email": "security-owner@example.co.ke"},
            ).status_code
            == 202
        )
    outbox = (
        db.query(EmailOutbox)
        .filter_by(template="password_reset")
        .order_by(EmailOutbox.created_at)
        .all()
    )
    tokens = [
        parse_qs(urlsplit(json.loads(decrypt_sensitive_value(row.payload_encrypted))["url"]).query)[
            "token"
        ][0]
        for row in outbox
    ]
    assert (
        client.post(
            f"{BASE}/auth/password-reset/confirm",
            json={"token": tokens[0], "new_password": "cannot-use-old-reset"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{BASE}/auth/password-reset/confirm",
            json={"token": tokens[1], "new_password": "valid-new-password"},
        ).status_code
        == 200
    )


def test_rotation_job_includes_mfa_and_encrypted_email_payloads(db, client, monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_ACTIVE_KEY_ID", "old")
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS_JSON", '{"old":"old-key"}')
    tokens = _register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.post(f"{BASE}/auth/mfa/setup", headers=headers).status_code == 201
    assert (
        client.post(
            f"{BASE}/auth/password-reset/request",
            json={"email": "security-owner@example.co.ke"},
        ).status_code
        == 202
    )
    assert encryption_key_version(db.query(MfaTotpCredential).one().secret_encrypted) == "old"
    assert {
        encryption_key_version(row.payload_encrypted) for row in db.query(EmailOutbox).all()
    } == {"old"}

    monkeypatch.setattr(settings, "ENCRYPTION_ACTIVE_KEY_ID", "new")
    monkeypatch.setattr(
        settings,
        "ENCRYPTION_KEYS_JSON",
        '{"old":"old-key","new":"new-key"}',
    )
    monkeypatch.setattr("app.rotate_encryption.WorkerSessionLocal", SessionLocal)
    assert rotate(apply=False)["mfa_credentials"] == 1
    counts = rotate(apply=True)
    assert counts["mfa_credentials"] == 1
    assert counts["email_payloads"] == 2

    db.expire_all()
    mfa = db.query(MfaTotpCredential).one()
    emails = db.query(EmailOutbox).all()
    assert encryption_key_version(mfa.secret_encrypted) == "new"
    assert {encryption_key_version(email.payload_encrypted) for email in emails} == {"new"}
    assert decrypt_sensitive_value(mfa.secret_encrypted)
    assert all(
        json.loads(decrypt_sensitive_value(email.payload_encrypted))["url"] for email in emails
    )
    actions = {row.action for row in db.query(AuditLog).all()}
    assert "mfa_secret_encryption_rotated" in actions
    assert "email_payload_encryption_rotated" in actions


def test_privileged_control_plane_requires_recent_mfa_when_enabled(
    db, client, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "REQUIRE_PRIVILEGED_MFA", True)
    denied = client.get(f"{BASE}/organization", headers=auth_headers)
    assert denied.status_code == 403
    assert "MFA-authenticated" in denied.json()["detail"]

    setup = client.post(f"{BASE}/auth/mfa/setup", headers=auth_headers)
    assert setup.status_code == 201, setup.text
    confirmed = client.post(
        f"{BASE}/auth/mfa/confirm",
        headers=auth_headers,
        json={"code": totp_code(setup.json()["secret"])},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert client.get(f"{BASE}/organization", headers=auth_headers).status_code == 200


def test_accountant_role_is_read_only_and_cannot_manage_merchants(db, client, auth_headers):
    user = db.query(User).one()
    user.role = "accountant"
    db.commit()
    assert client.get(f"{BASE}/payments", headers=auth_headers).status_code == 200
    assert client.get(f"{BASE}/callbacks", headers=auth_headers).status_code == 403
    denied = client.post(
        f"{BASE}/merchants",
        headers=auth_headers,
        json={
            "merchant_name": "Forbidden merchant",
            "shortcode": "888999",
            "shortcode_type": "paybill",
            "environment": "sandbox",
        },
    )
    assert denied.status_code == 403
