"""Controlled re-encryption job for moving ciphertext onto the active envelope key."""

from __future__ import annotations

import argparse

from app.core.config import settings
from app.core.security import encryption_key_version, reencrypt_sensitive_value
from app.database import WorkerSessionLocal
from app.models import (
    AuditLog,
    DarajaCredential,
    EmailOutbox,
    MerchantAccount,
    MfaTotpCredential,
    WebhookEndpoint,
)


def rotate(*, apply: bool) -> dict[str, int]:
    counts = {
        "credentials": 0,
        "webhook_endpoints": 0,
        "mfa_credentials": 0,
        "email_payloads": 0,
    }
    with WorkerSessionLocal() as db:
        credentials = db.query(DarajaCredential).all()
        for credential in credentials:
            fields = (
                "consumer_key_encrypted",
                "consumer_secret_encrypted",
                "passkey_encrypted",
                "initiator_name_encrypted",
                "security_credential_encrypted",
            )
            if all(
                not getattr(credential, field)
                or encryption_key_version(getattr(credential, field))
                == settings.ENCRYPTION_ACTIVE_KEY_ID
                for field in fields
            ):
                continue
            counts["credentials"] += 1
            if apply:
                for field in fields:
                    value = getattr(credential, field)
                    if value:
                        setattr(credential, field, reencrypt_sensitive_value(value))
                credential.encryption_key_version = settings.ENCRYPTION_ACTIVE_KEY_ID
                merchant = (
                    db.query(MerchantAccount).filter_by(id=credential.merchant_account_id).one()
                )
                db.add(
                    AuditLog(
                        organization_id=merchant.organization_id,
                        merchant_account_id=merchant.id,
                        action="credentials_encryption_rotated",
                        entity_type="daraja_credential",
                        entity_id=credential.id,
                        metadata_json={"key_version": settings.ENCRYPTION_ACTIVE_KEY_ID},
                    )
                )

        endpoints = db.query(WebhookEndpoint).all()
        for endpoint in endpoints:
            if (
                encryption_key_version(endpoint.secret_encrypted)
                == settings.ENCRYPTION_ACTIVE_KEY_ID
            ):
                continue
            counts["webhook_endpoints"] += 1
            if apply:
                endpoint.secret_encrypted = reencrypt_sensitive_value(endpoint.secret_encrypted)
                endpoint.encryption_key_version = settings.ENCRYPTION_ACTIVE_KEY_ID
                db.add(
                    AuditLog(
                        organization_id=endpoint.organization_id,
                        merchant_account_id=endpoint.merchant_account_id,
                        action="webhook_secret_encryption_rotated",
                        entity_type="webhook_endpoint",
                        entity_id=endpoint.id,
                        metadata_json={"key_version": settings.ENCRYPTION_ACTIVE_KEY_ID},
                    )
                )

        mfa_credentials = db.query(MfaTotpCredential).all()
        for credential in mfa_credentials:
            if (
                encryption_key_version(credential.secret_encrypted)
                == settings.ENCRYPTION_ACTIVE_KEY_ID
            ):
                continue
            counts["mfa_credentials"] += 1
            if apply:
                credential.secret_encrypted = reencrypt_sensitive_value(credential.secret_encrypted)
                credential.encryption_key_version = settings.ENCRYPTION_ACTIVE_KEY_ID
                db.add(
                    AuditLog(
                        organization_id=credential.organization_id,
                        actor_user_id=credential.user_id,
                        action="mfa_secret_encryption_rotated",
                        entity_type="mfa_totp_credential",
                        entity_id=credential.id,
                        metadata_json={"key_version": settings.ENCRYPTION_ACTIVE_KEY_ID},
                    )
                )

        email_payloads = db.query(EmailOutbox).all()
        for email in email_payloads:
            if encryption_key_version(email.payload_encrypted) == settings.ENCRYPTION_ACTIVE_KEY_ID:
                continue
            counts["email_payloads"] += 1
            if apply:
                email.payload_encrypted = reencrypt_sensitive_value(email.payload_encrypted)
                email.encryption_key_version = settings.ENCRYPTION_ACTIVE_KEY_ID
                db.add(
                    AuditLog(
                        organization_id=email.organization_id,
                        actor_user_id=email.user_id,
                        action="email_payload_encryption_rotated",
                        entity_type="email_outbox",
                        entity_id=email.id,
                        metadata_json={"key_version": settings.ENCRYPTION_ACTIVE_KEY_ID},
                    )
                )
        if apply:
            db.commit()
        else:
            db.rollback()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Rotate LynxPay encrypted fields")
    parser.add_argument("--apply", action="store_true", help="commit changes; default is dry-run")
    args = parser.parse_args()
    counts = rotate(apply=args.apply)
    mode = "rotated" if args.apply else "would rotate"
    print(
        f"{mode}: {counts['credentials']} credential records, "
        f"{counts['webhook_endpoints']} webhook endpoints, "
        f"{counts['mfa_credentials']} MFA credentials, "
        f"{counts['email_payloads']} email payloads"
    )


if __name__ == "__main__":
    main()
