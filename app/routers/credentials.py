"""LynxPay domain HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.security import (
    decrypt_sensitive_values,
    encrypt_sensitive_values,
    encryption_key_version,
)
from app.daraja import (
    DarajaClient,
)
from app.deps import (
    Principal,
    require_control_admin,
    scoped_merchant,
)
from app.models import (
    DarajaCredential,
    MerchantAccount,
)
from app.schemas import (
    DarajaCredentialPatch,
    DarajaCredentialWrite,
)
from app.security import masked_secret
from app.service import (
    active_credential,
    audit,
    decrypted_secrets,
    utcnow,
)

router = APIRouter(tags=["LynxPay"])


def _credential_view(credential: DarajaCredential) -> dict:
    return {
        "id": credential.id,
        "merchant_id": credential.merchant_account_id,
        "consumer_key": masked_secret(bool(credential.consumer_key_encrypted)),
        "consumer_secret": masked_secret(bool(credential.consumer_secret_encrypted)),
        "passkey": masked_secret(bool(credential.passkey_encrypted)),
        "initiator_name": masked_secret(bool(credential.initiator_name_encrypted)),
        "security_credential": masked_secret(bool(credential.security_credential_encrypted)),
        "shortcode": credential.shortcode,
        "environment": credential.environment,
        "is_active": credential.is_active,
        "last_tested_at": credential.last_tested_at.isoformat()
        if credential.last_tested_at
        else None,
        "created_at": credential.created_at.isoformat() if credential.created_at else None,
    }


def _write_credential(
    *,
    db: Session,
    merchant: MerchantAccount,
    values: dict,
    principal: Principal,
    request: Request,
    action: str,
) -> DarajaCredential:
    current = (
        db.query(DarajaCredential)
        .filter(
            DarajaCredential.merchant_account_id == merchant.id,
            DarajaCredential.is_active.is_(True),
        )
        .first()
    )
    if current:
        current.is_active = False
    encrypted_values = encrypt_sensitive_values(
        [
            values["consumer_key"],
            values["consumer_secret"],
            values["passkey"],
            values.get("initiator_name"),
            values.get("security_credential"),
        ]
    )
    encrypted_consumer_key = encrypted_values[0]
    credential = DarajaCredential(
        merchant_account_id=merchant.id,
        consumer_key_encrypted=encrypted_consumer_key,
        consumer_secret_encrypted=encrypted_values[1],
        passkey_encrypted=encrypted_values[2],
        shortcode=values["shortcode"],
        initiator_name_encrypted=encrypted_values[3],
        security_credential_encrypted=encrypted_values[4],
        environment=merchant.environment,
        encryption_key_version=encryption_key_version(encrypted_consumer_key),
        is_active=True,
    )
    db.add(credential)
    merchant.status = "credentials_added"
    db.flush()
    audit(
        db,
        organization_id=merchant.organization_id,
        merchant_id=merchant.id,
        action=action,
        entity_type="daraja_credential",
        entity_id=credential.id,
        principal=principal,
        request=request,
        metadata={"environment": merchant.environment, "shortcode": credential.shortcode},
    )
    db.commit()
    db.refresh(credential)
    return credential


@router.post("/merchants/{merchant_id}/daraja-credentials", status_code=201)
def create_daraja_credential(
    merchant_id: str,
    payload: DarajaCredentialWrite,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    merchant = scoped_merchant(db, principal, merchant_id)
    if payload.environment != merchant.environment:
        raise HTTPException(
            status_code=422, detail="Credential environment must match merchant environment"
        )
    if payload.shortcode != merchant.shortcode:
        raise HTTPException(
            status_code=422, detail="Credential shortcode must match merchant shortcode"
        )
    values = {
        key: (value.get_secret_value() if hasattr(value, "get_secret_value") else value)
        for key, value in payload.model_dump().items()
    }
    return _credential_view(
        _write_credential(
            db=db,
            merchant=merchant,
            values=values,
            principal=principal,
            request=request,
            action="credentials_added",
        )
    )


@router.patch("/merchants/{merchant_id}/daraja-credentials")
def update_daraja_credential(
    merchant_id: str,
    payload: DarajaCredentialPatch,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    merchant = scoped_merchant(db, principal, merchant_id)
    current = active_credential(db, merchant)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="At least one credential field is required")
    decrypted = decrypt_sensitive_values(
        [
            current.consumer_key_encrypted,
            current.consumer_secret_encrypted,
            current.passkey_encrypted,
            current.initiator_name_encrypted,
            current.security_credential_encrypted,
        ]
    )
    current_values = {
        "consumer_key": decrypted[0],
        "consumer_secret": decrypted[1],
        "passkey": decrypted[2],
        "shortcode": current.shortcode,
        "initiator_name": decrypted[3],
        "security_credential": decrypted[4],
    }
    for key, value in updates.items():
        current_values[key] = (
            value.get_secret_value() if hasattr(value, "get_secret_value") else value
        )
    if current_values["shortcode"] != merchant.shortcode:
        raise HTTPException(
            status_code=422, detail="Credential shortcode must match merchant shortcode"
        )
    return _credential_view(
        _write_credential(
            db=db,
            merchant=merchant,
            values=current_values,
            principal=principal,
            request=request,
            action="credentials_updated",
        )
    )


@router.post("/merchants/{merchant_id}/daraja-credentials/test")
async def test_daraja_credential(
    merchant_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    merchant = scoped_merchant(db, principal, merchant_id)
    credential = active_credential(db, merchant)
    try:
        await DarajaClient(merchant.environment).get_access_token(decrypted_secrets(credential))
    except Exception:
        audit(
            db,
            organization_id=merchant.organization_id,
            merchant_id=merchant.id,
            action="credentials_test_failed",
            entity_type="daraja_credential",
            entity_id=credential.id,
            principal=principal,
            request=request,
        )
        db.commit()
        raise HTTPException(status_code=502, detail="Daraja credential test failed") from None
    credential.last_tested_at = utcnow()
    merchant.status = "verified"
    audit(
        db,
        organization_id=merchant.organization_id,
        merchant_id=merchant.id,
        action="credentials_tested",
        entity_type="daraja_credential",
        entity_id=credential.id,
        principal=principal,
        request=request,
    )
    db.commit()
    return {
        "status": "valid",
        "merchant_status": merchant.status,
        "tested_at": credential.last_tested_at.isoformat(),
    }


@router.delete("/merchants/{merchant_id}/daraja-credentials", status_code=204)
def disable_daraja_credential(
    merchant_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    merchant = scoped_merchant(db, principal, merchant_id)
    credential = active_credential(db, merchant)
    credential.is_active = False
    merchant.status = "pending_setup"
    audit(
        db,
        organization_id=merchant.organization_id,
        merchant_id=merchant.id,
        action="credential_disabled",
        entity_type="daraja_credential",
        entity_id=credential.id,
        principal=principal,
        request=request,
    )
    db.commit()
    return None
