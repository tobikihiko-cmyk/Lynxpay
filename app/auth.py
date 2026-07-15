"""Native LynxPay authentication, MFA, reset, and revocable sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.datetime_utils import ensure_utc_datetime
from app.core.deps import get_client_ip
from app.core.security import (
    create_access_token,
    decrypt_sensitive_value,
    encrypt_sensitive_value,
    encryption_key_version,
    generate_refresh_token,
    generate_totp_secret,
    hash_opaque_token,
    hash_password,
    matching_totp_step,
    refresh_token_prefix,
    totp_uri,
    verify_password,
)
from app.database import get_db
from app.deps import Principal, get_principal
from app.email_delivery import enqueue_email
from app.models import (
    AuditLog,
    AuthSession,
    EmailVerificationToken,
    MfaTotpCredential,
    Organization,
    PasswordResetToken,
    User,
)
from app.schemas import normalize_kenyan_phone
from app.service import audit

router = APIRouter(prefix="/auth", tags=["Authentication"])
OAUTH_SCHEME = "bearer"


class RegisterRequest(BaseModel):
    organization_name: str = Field(min_length=2, max_length=200)
    legal_name: str | None = Field(None, max_length=250)
    contact_email: EmailStr
    contact_phone: str | None = None
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("contact_phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        return normalize_kenyan_phone(value) if value else None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    mfa_code: str | None = Field(None, min_length=6, max_length=30)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=40, max_length=500)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=30, max_length=500)
    new_password: str = Field(min_length=12, max_length=128)


class EmailVerificationRequest(BaseModel):
    email: EmailStr


class EmailVerificationConfirm(BaseModel):
    token: str = Field(min_length=30, max_length=500)


class MfaCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=30)


def _user_view(user: User) -> dict:
    return {
        "id": user.id,
        "organization_id": user.organization_id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "email_verified": bool(user.email_verified_at),
        "email_verified_at": user.email_verified_at.isoformat() if user.email_verified_at else None,
        "is_platform_admin": bool(user.is_platform_admin),
    }


def _request_context(request: Request) -> tuple[str | None, str | None]:
    return get_client_ip(request), request.headers.get("user-agent", "")[:500] or None


def _issue_tokens(
    db: Session,
    user: User,
    request: Request,
    *,
    family_id: str | None = None,
) -> tuple[dict, AuthSession]:
    raw_refresh, prefix, digest = generate_refresh_token()
    ip_address, user_agent = _request_context(request)
    session = AuthSession(
        organization_id=user.organization_id,
        user_id=user.id,
        family_id=family_id or str(uuid.uuid4()),
        refresh_token_prefix=prefix,
        refresh_token_hash=digest,
        status="active",
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(session)
    db.flush()
    return (
        {
            "access_token": create_access_token(user.id, session.id),
            "refresh_token": raw_refresh,
            "token_type": OAUTH_SCHEME,
            "expires_in": 60 * settings.ACCESS_TOKEN_EXPIRE_MINUTES,
            "refresh_expires_in": 86400 * settings.REFRESH_TOKEN_EXPIRE_DAYS,
            "user": _user_view(user),
        },
        session,
    )


def _queue_email_verification(db: Session, user: User) -> EmailVerificationToken:
    db.query(EmailVerificationToken).filter(
        EmailVerificationToken.user_id == user.id,
        EmailVerificationToken.status == "pending",
    ).update({"status": "revoked"}, synchronize_session=False)
    raw_token = f"lpverify_{secrets.token_urlsafe(40)}"
    record = EmailVerificationToken(
        organization_id=user.organization_id,
        user_id=user.id,
        token_hash=hash_opaque_token(raw_token),
        status="pending",
        expires_at=datetime.now(timezone.utc)
        + timedelta(hours=settings.EMAIL_VERIFICATION_EXPIRE_HOURS),
    )
    db.add(record)
    db.flush()
    enqueue_email(
        db,
        organization_id=user.organization_id,
        user_id=user.id,
        to_email=user.email,
        template="email_verification",
        payload={
            "url": f"{settings.DASHBOARD_PUBLIC_URL.rstrip('/')}/verify-email?token={raw_token}",
            "expires_at": record.expires_at.isoformat(),
        },
    )
    return record


def _verify_mfa(db: Session, user: User, code: str | None) -> bool:
    credential = (
        db.query(MfaTotpCredential)
        .filter(MfaTotpCredential.user_id == user.id, MfaTotpCredential.enabled.is_(True))
        .with_for_update()
        .first()
    )
    if not credential:
        return True
    if not code:
        return False
    secret = decrypt_sensitive_value(credential.secret_encrypted)
    step = matching_totp_step(secret, code) if secret else None
    if step is not None and (credential.last_used_step is None or step > credential.last_used_step):
        credential.last_used_at = datetime.now(timezone.utc)
        credential.last_used_step = step
        return True
    digest = hash_opaque_token(code.upper().replace("-", ""))
    recovery = list(credential.recovery_code_hashes or [])
    if digest in recovery:
        recovery.remove(digest)
        credential.recovery_code_hashes = recovery
        credential.last_used_at = datetime.now(timezone.utc)
        return True
    return False


@router.post("/register", status_code=201)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    email = str(payload.contact_email).lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="Email is already registered")
    organization = Organization(
        name=payload.organization_name,
        legal_name=payload.legal_name,
        contact_email=email,
        contact_phone=payload.contact_phone,
        status="active",
    )
    db.add(organization)
    db.flush()
    user = User(
        organization_id=organization.id,
        email=email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role="owner",
        status="active",
    )
    db.add(user)
    db.flush()
    verification = _queue_email_verification(db, user)
    response, session = _issue_tokens(db, user, request)
    response["email_verification_required"] = True
    db.add(
        AuditLog(
            organization_id=organization.id,
            actor_user_id=user.id,
            action="organization_created",
            entity_type="organization",
            entity_id=organization.id,
            metadata_json={"owner_user_id": user.id, "session_id": session.id},
        )
    )
    db.add(
        AuditLog(
            organization_id=organization.id,
            actor_user_id=user.id,
            action="email_verification_queued",
            entity_type="email_verification_token",
            entity_id=verification.id,
        )
    )
    db.commit()
    return response


@router.post("/email-verification/request", status_code=202)
def request_email_verification(payload: EmailVerificationRequest, db: Session = Depends(get_db)):
    user = (
        db.query(User)
        .filter(User.email == str(payload.email).lower(), User.status == "active")
        .first()
    )
    if user and not user.email_verified_at:
        record = _queue_email_verification(db, user)
        db.add(
            AuditLog(
                organization_id=user.organization_id,
                actor_user_id=user.id,
                action="email_verification_queued",
                entity_type="email_verification_token",
                entity_id=record.id,
            )
        )
        db.commit()
    return {"detail": "If verification is required, a new email has been queued."}


@router.post("/email-verification/confirm")
def confirm_email_verification(payload: EmailVerificationConfirm, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    record = (
        db.query(EmailVerificationToken)
        .filter(EmailVerificationToken.token_hash == hash_opaque_token(payload.token))
        .with_for_update()
        .first()
    )
    if not record or record.status != "pending" or ensure_utc_datetime(record.expires_at) <= now:
        raise HTTPException(
            status_code=400, detail="Email verification token is invalid or expired"
        )
    user = db.query(User).filter(User.id == record.user_id, User.status == "active").first()
    if not user:
        raise HTTPException(
            status_code=400, detail="Email verification token is invalid or expired"
        )
    user.email_verified_at = now
    record.status = "used"
    record.used_at = now
    db.query(EmailVerificationToken).filter(
        EmailVerificationToken.user_id == user.id,
        EmailVerificationToken.id != record.id,
        EmailVerificationToken.status == "pending",
    ).update({"status": "revoked"}, synchronize_session=False)
    db.add(
        AuditLog(
            organization_id=user.organization_id,
            actor_user_id=user.id,
            action="email_verified",
            entity_type="user",
            entity_id=user.id,
        )
    )
    db.commit()
    return {"verified": True, "email": user.email, "verified_at": now.isoformat()}


@router.post("/login")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == str(payload.email).lower()).first()
    if (
        not user
        or user.status != "active"
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not _verify_mfa(db, user, payload.mfa_code):
        raise HTTPException(status_code=401, detail="A valid MFA or recovery code is required")
    user.last_login_at = datetime.now(timezone.utc)
    response, session = _issue_tokens(db, user, request)
    db.add(
        AuditLog(
            organization_id=user.organization_id,
            actor_user_id=user.id,
            action="session_created",
            entity_type="auth_session",
            entity_id=session.id,
        )
    )
    db.commit()
    return response


@router.post("/refresh")
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    prefix = refresh_token_prefix(payload.refresh_token)
    record = (
        db.query(AuthSession)
        .filter(AuthSession.refresh_token_prefix == prefix)
        .with_for_update()
        .first()
        if prefix
        else None
    )
    valid_hash = bool(
        record
        and secrets.compare_digest(
            record.refresh_token_hash, hash_opaque_token(payload.refresh_token)
        )
    )
    now = datetime.now(timezone.utc)
    if not record or not valid_hash:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if record.status != "active":
        db.query(AuthSession).filter(
            AuthSession.family_id == record.family_id, AuthSession.status == "active"
        ).update({"status": "revoked", "revoked_at": now}, synchronize_session=False)
        db.commit()
        raise HTTPException(
            status_code=401, detail="Refresh token reuse detected; session family revoked"
        )
    if ensure_utc_datetime(record.expires_at) <= now:
        record.status = "expired"
        db.commit()
        raise HTTPException(status_code=401, detail="Refresh token expired")
    user = db.query(User).filter(User.id == record.user_id, User.status == "active").first()
    if not user:
        raise HTTPException(status_code=401, detail="User is unavailable")
    response, replacement = _issue_tokens(db, user, request, family_id=record.family_id)
    record.status = "rotated"
    record.last_used_at = now
    record.replaced_by_session_id = replacement.id
    db.add(
        AuditLog(
            organization_id=user.organization_id,
            actor_user_id=user.id,
            action="refresh_token_rotated",
            entity_type="auth_session",
            entity_id=replacement.id,
            metadata_json={"replaced_session_id": record.id},
        )
    )
    db.commit()
    return response


@router.post("/logout", status_code=204)
def logout(payload: RefreshRequest, db: Session = Depends(get_db)):
    prefix = refresh_token_prefix(payload.refresh_token)
    record = (
        db.query(AuthSession).filter(AuthSession.refresh_token_prefix == prefix).first()
        if prefix
        else None
    )
    if record and secrets.compare_digest(
        record.refresh_token_hash, hash_opaque_token(payload.refresh_token)
    ):
        record.status = "revoked"
        record.revoked_at = datetime.now(timezone.utc)
        db.commit()


@router.post("/password-reset/request", status_code=202)
def request_password_reset(payload: PasswordResetRequest, db: Session = Depends(get_db)):
    user = (
        db.query(User)
        .filter(User.email == str(payload.email).lower(), User.status == "active")
        .first()
    )
    if user:
        db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.status == "pending",
        ).update({"status": "revoked"}, synchronize_session=False)
        raw_token = f"lpreset_{secrets.token_urlsafe(40)}"
        reset = PasswordResetToken(
            organization_id=user.organization_id,
            user_id=user.id,
            token_hash=hash_opaque_token(raw_token),
            status="pending",
            expires_at=datetime.now(timezone.utc)
            + timedelta(minutes=settings.PASSWORD_RESET_EXPIRE_MINUTES),
        )
        db.add(reset)
        db.flush()
        enqueue_email(
            db,
            organization_id=user.organization_id,
            user_id=user.id,
            to_email=user.email,
            template="password_reset",
            payload={
                "url": f"{settings.DASHBOARD_PUBLIC_URL.rstrip('/')}/reset-password?token={raw_token}"
            },
        )
        db.add(
            AuditLog(
                organization_id=user.organization_id,
                actor_user_id=user.id,
                action="password_reset_requested",
                entity_type="password_reset_token",
                entity_id=reset.id,
            )
        )
        db.commit()
    return {"detail": "If the account exists, password reset instructions have been queued."}


@router.post("/password-reset/confirm")
def confirm_password_reset(payload: PasswordResetConfirm, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    record = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == hash_opaque_token(payload.token))
        .with_for_update()
        .first()
    )
    if not record or record.status != "pending" or ensure_utc_datetime(record.expires_at) <= now:
        raise HTTPException(status_code=400, detail="Password reset token is invalid or expired")
    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="Password reset token is invalid or expired")
    user.password_hash = hash_password(payload.new_password)
    record.status = "used"
    record.used_at = now
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.id != record.id,
        PasswordResetToken.status == "pending",
    ).update({"status": "revoked"}, synchronize_session=False)
    db.query(AuthSession).filter(
        AuthSession.user_id == user.id, AuthSession.status == "active"
    ).update({"status": "revoked", "revoked_at": now}, synchronize_session=False)
    db.add(
        AuditLog(
            organization_id=user.organization_id,
            actor_user_id=user.id,
            action="password_reset_completed",
            entity_type="user",
            entity_id=user.id,
        )
    )
    db.commit()
    return {"detail": "Password reset completed; all sessions were revoked."}


@router.post("/mfa/setup", status_code=201)
def setup_mfa(
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    if not principal.user_id:
        raise HTTPException(status_code=403, detail="User authentication is required")
    user = db.query(User).filter(User.id == principal.user_id).one()
    existing = db.query(MfaTotpCredential).filter(MfaTotpCredential.user_id == user.id).first()
    if existing and existing.enabled:
        raise HTTPException(status_code=409, detail="MFA is already enabled")
    if existing:
        db.delete(existing)
        db.flush()
    secret = generate_totp_secret()
    encrypted = encrypt_sensitive_value(secret)
    recovery_codes = [secrets.token_hex(5).upper() for _ in range(10)]
    credential = MfaTotpCredential(
        organization_id=user.organization_id,
        user_id=user.id,
        secret_encrypted=encrypted,
        encryption_key_version=encryption_key_version(encrypted),
        recovery_code_hashes=[hash_opaque_token(code) for code in recovery_codes],
        enabled=False,
    )
    db.add(credential)
    db.flush()
    audit(
        db,
        organization_id=user.organization_id,
        merchant_id=None,
        action="mfa_setup_started",
        entity_type="mfa_totp_credential",
        entity_id=credential.id,
        principal=principal,
        request=request,
    )
    db.commit()
    return {
        "secret": secret,
        "provisioning_uri": totp_uri(secret, user.email),
        "recovery_codes": recovery_codes,
    }


@router.post("/mfa/confirm")
def confirm_mfa(
    payload: MfaCodeRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    credential = (
        db.query(MfaTotpCredential).filter(MfaTotpCredential.user_id == principal.user_id).first()
    )
    secret = decrypt_sensitive_value(credential.secret_encrypted) if credential else None
    step = matching_totp_step(secret, payload.code) if secret else None
    if not credential or credential.enabled or step is None:
        raise HTTPException(status_code=400, detail="Invalid MFA confirmation code")
    credential.enabled = True
    credential.confirmed_at = datetime.now(timezone.utc)
    credential.last_used_step = step
    audit(
        db,
        organization_id=principal.organization_id,
        merchant_id=None,
        action="mfa_enabled",
        entity_type="user",
        entity_id=principal.user_id,
        principal=principal,
        request=request,
    )
    db.commit()
    return {"enabled": True}


@router.delete("/mfa", status_code=204)
def disable_mfa(
    payload: MfaCodeRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == principal.user_id).one()
    if not _verify_mfa(db, user, payload.code):
        raise HTTPException(status_code=401, detail="A valid MFA or recovery code is required")
    credential = db.query(MfaTotpCredential).filter(MfaTotpCredential.user_id == user.id).one()
    db.delete(credential)
    audit(
        db,
        organization_id=user.organization_id,
        merchant_id=None,
        action="mfa_disabled",
        entity_type="user",
        entity_id=user.id,
        principal=principal,
        request=request,
    )
    db.commit()


@router.get("/sessions")
def list_sessions(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not principal.user_id:
        raise HTTPException(status_code=403, detail="User authentication is required")
    rows = (
        db.query(AuthSession)
        .filter(AuthSession.user_id == principal.user_id)
        .order_by(AuthSession.created_at.desc())
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "status": row.status,
                "ip_address": row.ip_address,
                "user_agent": row.user_agent,
                "created_at": row.created_at.isoformat(),
                "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
                "expires_at": row.expires_at.isoformat(),
            }
            for row in rows
        ]
    }


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
):
    record = (
        db.query(AuthSession)
        .filter(AuthSession.id == session_id, AuthSession.user_id == principal.user_id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Session not found")
    record.status = "revoked"
    record.revoked_at = datetime.now(timezone.utc)
    db.commit()


@router.delete("/sessions", status_code=204)
def revoke_all_sessions(
    principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
):
    now = datetime.now(timezone.utc)
    db.query(AuthSession).filter(
        AuthSession.user_id == principal.user_id, AuthSession.status == "active"
    ).update({"status": "revoked", "revoked_at": now}, synchronize_session=False)
    db.commit()


@router.get("/me")
def me(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not principal.user_id:
        raise HTTPException(status_code=403, detail="User authentication is required")
    user = db.query(User).filter(User.id == principal.user_id).first()
    return _user_view(user)
