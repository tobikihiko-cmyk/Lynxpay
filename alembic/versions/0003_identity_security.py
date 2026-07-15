"""add revocable sessions, password reset, MFA, and encrypted email outbox

Revision ID: 0003_identity_security
Revises: 0002_phase2_reliability
Create Date: 2026-07-15 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_identity_security"
down_revision: str | None = "0002_phase2_reliability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "lynxpay_auth_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("family_id", sa.String(36), nullable=False),
        sa.Column("refresh_token_prefix", sa.String(32), nullable=False, unique=True),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "replaced_by_session_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_auth_sessions.id", ondelete="SET NULL"),
        ),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in (
        "organization_id",
        "user_id",
        "family_id",
        "refresh_token_prefix",
        "status",
        "expires_at",
    ):
        op.create_index(
            f"ix_lynxpay_auth_sessions_{column}",
            "lynxpay_auth_sessions",
            [column],
            unique=column == "refresh_token_prefix",
        )

    op.create_table(
        "lynxpay_password_reset_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("organization_id", "user_id", "token_hash", "expires_at"):
        op.create_index(
            f"ix_lynxpay_password_reset_tokens_{column}",
            "lynxpay_password_reset_tokens",
            [column],
            unique=column == "token_hash",
        )

    op.create_table(
        "lynxpay_mfa_totp_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("encryption_key_version", sa.String(50), nullable=False),
        sa.Column("recovery_code_hashes", JSON_TYPE, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_lynxpay_mfa_totp_credentials_organization_id",
        "lynxpay_mfa_totp_credentials",
        ["organization_id"],
    )
    op.create_index(
        "ix_lynxpay_mfa_totp_credentials_user_id",
        "lynxpay_mfa_totp_credentials",
        ["user_id"],
        unique=True,
    )

    op.create_table(
        "lynxpay_email_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("lynxpay_users.id", ondelete="SET NULL")),
        sa.Column("to_email", sa.String(254), nullable=False),
        sa.Column("template", sa.String(50), nullable=False),
        sa.Column("payload_encrypted", sa.Text(), nullable=False),
        sa.Column("encryption_key_version", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("lease_owner", sa.String(100)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("organization_id", "user_id", "status", "next_attempt_at"):
        op.create_index(f"ix_lynxpay_email_outbox_{column}", "lynxpay_email_outbox", [column])


def downgrade() -> None:
    op.drop_table("lynxpay_email_outbox")
    op.drop_table("lynxpay_mfa_totp_credentials")
    op.drop_table("lynxpay_password_reset_tokens")
    op.drop_table("lynxpay_auth_sessions")
