"""Native LynxPay team membership and invitation management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.database import get_db, set_tenant_context
from app.deps import Principal, require_control_admin
from app.email_delivery import enqueue_email
from app.models import AuditLog, Organization, TeamInvitation, User
from app.service import audit

router = APIRouter(prefix="/team", tags=["Team"])
public_router = APIRouter(prefix="/auth/invitations", tags=["Authentication"])


class InvitationCreate(BaseModel):
    email: EmailStr
    role: str = Field(
        default="operator",
        pattern="^(admin|operator|developer|support|accountant|read_only)$",
    )
    expires_in_days: int = Field(default=7, ge=1, le=30)


class InvitationAccept(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=12, max_length=128)


class MemberUpdate(BaseModel):
    role: str | None = Field(
        None,
        pattern="^(owner|admin|operator|developer|support|accountant|read_only)$",
    )
    status: str | None = Field(None, pattern="^(active|inactive)$")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _member_view(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "status": user.status,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("/users")
def list_members(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    rows = (
        db.query(User)
        .filter(User.organization_id == principal.organization_id)
        .order_by(User.created_at)
        .all()
    )
    return {"items": [_member_view(row) for row in rows]}


@router.post("/invitations", status_code=201)
def create_invitation(
    payload: InvitationCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    email = str(payload.email).lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="Email is already a LynxPay user")
    token = f"lpi_{secrets.token_urlsafe(32)}"
    invitation = TeamInvitation(
        organization_id=principal.organization_id,
        email=email,
        role=payload.role,
        token_hash=_token_hash(token),
        status="pending",
        invited_by_user_id=principal.user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days),
    )
    db.add(invitation)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A pending invitation already exists") from None
    audit(
        db,
        organization_id=principal.organization_id,
        merchant_id=None,
        action="team_invitation_created",
        entity_type="team_invitation",
        entity_id=invitation.id,
        principal=principal,
        request=request,
        metadata={"email": email, "role": payload.role},
    )
    organization = db.query(Organization).filter(Organization.id == principal.organization_id).one()
    enqueue_email(
        db,
        organization_id=principal.organization_id,
        user_id=None,
        to_email=email,
        template="team_invitation",
        payload={
            "organization_name": organization.name,
            "url": f"{settings.DASHBOARD_PUBLIC_URL.rstrip('/')}/accept-invitation?token={token}",
            "expires_at": invitation.expires_at.isoformat(),
        },
    )
    db.commit()
    response = {
        "id": invitation.id,
        "email": invitation.email,
        "role": invitation.role,
        "expires_at": invitation.expires_at.isoformat(),
    }
    if not settings.is_production:
        response["invitation_token"] = token
        response["warning"] = "Development-only token display; production delivers by email."
    return response


@router.get("/invitations")
def list_invitations(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    rows = (
        db.query(TeamInvitation)
        .filter(TeamInvitation.organization_id == principal.organization_id)
        .order_by(TeamInvitation.created_at.desc())
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "email": row.email,
                "role": row.role,
                "status": row.status,
                "expires_at": row.expires_at.isoformat(),
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }


@router.delete("/invitations/{invitation_id}", status_code=204)
def revoke_invitation(
    invitation_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    invitation = (
        db.query(TeamInvitation)
        .filter(
            TeamInvitation.id == invitation_id,
            TeamInvitation.organization_id == principal.organization_id,
            TeamInvitation.status == "pending",
        )
        .first()
    )
    if not invitation:
        raise HTTPException(status_code=404, detail="Pending invitation not found")
    invitation.status = "revoked"
    audit(
        db,
        organization_id=principal.organization_id,
        merchant_id=None,
        action="team_invitation_revoked",
        entity_type="team_invitation",
        entity_id=invitation.id,
        principal=principal,
        request=request,
    )
    db.commit()


@router.patch("/users/{user_id}")
def update_member(
    user_id: str,
    payload: MemberUpdate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_control_admin),
):
    member = (
        db.query(User)
        .filter(User.id == user_id, User.organization_id == principal.organization_id)
        .first()
    )
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="At least one field is required")
    removes_owner = member.role == "owner" and (
        changes.get("role", "owner") != "owner" or changes.get("status", member.status) != "active"
    )
    if removes_owner:
        owner_count = (
            db.query(User)
            .filter(
                User.organization_id == principal.organization_id,
                User.role == "owner",
                User.status == "active",
            )
            .count()
        )
        if owner_count <= 1:
            raise HTTPException(status_code=409, detail="Organization must retain an active owner")
    for field, value in changes.items():
        setattr(member, field, value)
    audit(
        db,
        organization_id=principal.organization_id,
        merchant_id=None,
        action="team_member_updated",
        entity_type="user",
        entity_id=member.id,
        principal=principal,
        request=request,
        metadata={"changed_fields": list(changes)},
    )
    db.commit()
    db.refresh(member)
    return _member_view(member)


@public_router.post("/{token}/accept", status_code=201)
def accept_invitation(token: str, payload: InvitationAccept, db: Session = Depends(get_db)):
    invitation = (
        db.query(TeamInvitation).filter(TeamInvitation.token_hash == _token_hash(token)).first()
    )
    now = datetime.now(timezone.utc)
    if (
        not invitation
        or invitation.status != "pending"
        or invitation.expires_at.replace(tzinfo=invitation.expires_at.tzinfo or timezone.utc) <= now
    ):
        raise HTTPException(status_code=404, detail="Invitation is invalid or expired")
    if db.query(User).filter(User.email == invitation.email).first():
        raise HTTPException(status_code=409, detail="Email is already registered")
    set_tenant_context(db, invitation.organization_id)
    user = User(
        organization_id=invitation.organization_id,
        email=invitation.email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=invitation.role,
        status="active",
    )
    db.add(user)
    db.flush()
    invitation.status = "accepted"
    invitation.accepted_at = now
    db.add(
        AuditLog(
            organization_id=invitation.organization_id,
            actor_user_id=user.id,
            action="team_invitation_accepted",
            entity_type="user",
            entity_id=user.id,
            metadata_json={"invitation_id": invitation.id},
        )
    )
    db.commit()
    return _member_view(user)
