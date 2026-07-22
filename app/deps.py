"""Authentication and tenant-context dependencies for LynxPay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.datetime_utils import ensure_utc_datetime
from app.core.deps import get_db
from app.core.security import decode_token
from app.database import set_tenant_context
from app.models import ApiKey, AuthSession, MerchantAccount, User
from app.security import api_key_prefix, verify_api_key


@dataclass(frozen=True)
class Principal:
    organization_id: str | None
    user_id: str | None = None
    api_key_id: str | None = None
    merchant_id: str | None = None
    environment: str | None = None
    scopes: frozenset[str] = frozenset()
    is_control_plane_admin: bool = False
    is_platform_admin: bool = False
    role: str | None = None
    session_id: str | None = None
    mfa_authenticated: bool = False


ROLE_SCOPES: dict[str, frozenset[str]] = {
    "owner": frozenset(
        {
            "merchants:read",
            "payments:read",
            "payments:write",
            "callbacks:read",
            "callbacks:read_raw",
            "webhooks:read",
            "webhooks:write",
            "audit:read",
        }
    ),
    "admin": frozenset(
        {
            "merchants:read",
            "payments:read",
            "payments:write",
            "callbacks:read",
            "callbacks:read_raw",
            "webhooks:read",
            "webhooks:write",
            "audit:read",
        }
    ),
    "operator": frozenset(
        {
            "merchants:read",
            "payments:read",
            "payments:write",
            "callbacks:read",
            "webhooks:read",
            "audit:read",
        }
    ),
    "member": frozenset({"merchants:read", "payments:read", "callbacks:read"}),
    "developer": frozenset(
        {
            "merchants:read",
            "payments:read",
            "payments:write",
            "callbacks:read",
            "webhooks:read",
            "webhooks:write",
        }
    ),
    "support": frozenset(
        {
            "merchants:read",
            "payments:read",
            "callbacks:read",
            "callbacks:read_raw",
            "webhooks:read",
            "audit:read",
        }
    ),
    "accountant": frozenset({"merchants:read", "payments:read", "audit:read"}),
    "read_only": frozenset(
        {
            "merchants:read",
            "payments:read",
            "callbacks:read",
            "webhooks:read",
            "audit:read",
        }
    ),
}


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid LynxPay authentication is required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    authorization = request.headers.get("Authorization", "").strip()
    x_api_key = request.headers.get("X-API-Key", "").strip()
    token = x_api_key or (authorization[7:] if authorization.lower().startswith("bearer ") else "")
    if not token:
        raise _unauthorized()

    prefix = api_key_prefix(token)
    if prefix:
        record = (
            db.query(ApiKey).filter(ApiKey.key_prefix == prefix, ApiKey.status == "active").first()
        )
        now = datetime.now(timezone.utc)  # - deployed Python 3.10 compatibility
        if (
            not record
            or not verify_api_key(token, record.key_hash)
            or (record.expires_at is not None and ensure_utc_datetime(record.expires_at) <= now)
        ):
            raise _unauthorized()
        record.last_used_at = now
        db.commit()
        set_tenant_context(db, record.organization_id)
        return Principal(
            organization_id=record.organization_id,
            api_key_id=record.id,
            merchant_id=record.merchant_account_id,
            environment=record.environment,
            scopes=frozenset(record.scopes or []),
        )

    payload = decode_token(token)
    if not payload:
        raise _unauthorized()
    try:
        user_id = str(payload["sub"])
        session_id = str(payload["sid"])
    except (KeyError, TypeError, ValueError):
        raise _unauthorized() from None
    session = (
        db.query(AuthSession)
        .filter(
            AuthSession.id == session_id,
            AuthSession.user_id == user_id,
            AuthSession.status == "active",
        )
        .first()
    )
    if not session or ensure_utc_datetime(session.expires_at) <= datetime.now(timezone.utc):
        raise _unauthorized()
    user = db.query(User).filter(User.id == user_id, User.status == "active").first()
    if not user:
        raise _unauthorized()
    mfa_authenticated = bool(
        session.mfa_authenticated_at
        and ensure_utc_datetime(session.mfa_authenticated_at)
        >= datetime.now(timezone.utc) - timedelta(minutes=settings.MFA_PRIVILEGED_MAX_AGE_MINUTES)
    )
    set_tenant_context(db, user.organization_id)
    return Principal(
        organization_id=user.organization_id,
        user_id=user.id,
        scopes=ROLE_SCOPES.get(user.role, frozenset()),
        is_control_plane_admin=user.role in {"owner", "admin"},
        is_platform_admin=bool(user.is_platform_admin),
        role=user.role,
        session_id=session.id,
        mfa_authenticated=mfa_authenticated,
    )


def require_scope(scope: str):
    def _require(principal: Principal = Depends(get_principal)) -> Principal:
        implied = scope == "webhooks:read" and "webhooks:write" in principal.scopes
        if "*" not in principal.scopes and scope not in principal.scopes and not implied:
            raise HTTPException(status_code=403, detail=f"API key lacks required scope: {scope}")
        if not principal.organization_id:
            raise HTTPException(
                status_code=403, detail="LynxPay organization membership is required"
            )
        return principal

    return _require


def require_control_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_control_plane_admin:
        raise HTTPException(
            status_code=403, detail="Control-plane administrator access is required"
        )
    ensure_privileged_mfa(principal)
    return principal


def require_platform_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.user_id or not principal.is_platform_admin:
        raise HTTPException(
            status_code=403, detail="LynxPay platform administrator access is required"
        )
    ensure_privileged_mfa(principal)
    return principal


def ensure_privileged_mfa(principal: Principal) -> None:
    if settings.REQUIRE_PRIVILEGED_MFA and (
        not principal.user_id or not principal.mfa_authenticated
    ):
        raise HTTPException(
            status_code=403,
            detail="A recent MFA-authenticated session is required for this operation",
        )


def scoped_merchant(db: Session, principal: Principal, merchant_id: str) -> MerchantAccount:
    query = db.query(MerchantAccount).filter(
        MerchantAccount.id == merchant_id,
        MerchantAccount.organization_id == principal.organization_id,
    )
    if principal.merchant_id:
        query = query.filter(MerchantAccount.id == principal.merchant_id)
    if principal.api_key_id and principal.environment:
        query = query.filter(MerchantAccount.environment == principal.environment)
    merchant = query.first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")
    return merchant
