"""add Version 2 email verification, consent, and production approval

Revision ID: 0007_v2_onboarding_approval
Revises: 0006_v2_payment_evidence
Create Date: 2026-07-16 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_v2_onboarding_approval"
down_revision: str | None = "0006_v2_payment_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("lynxpay_organizations") as batch:
        batch.add_column(sa.Column("terms_accepted_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("privacy_accepted_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("accepted_terms_version", sa.String(50)))
        batch.add_column(sa.Column("accepted_privacy_version", sa.String(50)))

    with op.batch_alter_table("lynxpay_users") as batch:
        batch.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    op.create_table(
        "lynxpay_email_verification_tokens",
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
    op.create_index(
        "ix_lynxpay_email_verification_tokens_organization_id",
        "lynxpay_email_verification_tokens",
        ["organization_id"],
    )
    op.create_index(
        "ix_lynxpay_email_verification_tokens_user_id",
        "lynxpay_email_verification_tokens",
        ["user_id"],
    )
    op.create_index(
        "ix_lynxpay_email_verification_tokens_token_hash",
        "lynxpay_email_verification_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_lynxpay_email_verification_tokens_status",
        "lynxpay_email_verification_tokens",
        ["status"],
    )
    op.create_index(
        "ix_lynxpay_email_verification_tokens_expires_at",
        "lynxpay_email_verification_tokens",
        ["expires_at"],
    )

    with op.batch_alter_table("lynxpay_merchant_accounts") as batch:
        batch.drop_constraint("ck_lynxpay_merchant_status", type_="check")
        batch.create_check_constraint(
            "ck_lynxpay_merchant_status",
            "status IN ('active','inactive','suspended','pending_setup','credentials_added','verified','pending_approval','rejected')",
        )
        batch.add_column(sa.Column("approval_submitted_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("approved_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column(
                "approved_by_user_id",
                sa.String(36),
                sa.ForeignKey("lynxpay_users.id", ondelete="SET NULL"),
            )
        )
        batch.add_column(sa.Column("rejected_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("rejection_reason", sa.String(500)))
        batch.add_column(sa.Column("suspended_at", sa.DateTime(timezone=True)))
        batch.create_index(
            "ix_lynxpay_merchant_approval_queue", ["environment", "status", "created_at"]
        )

    with op.batch_alter_table("lynxpay_api_keys") as batch:
        batch.add_column(
            sa.Column(
                "created_by_user_id",
                sa.String(36),
                sa.ForeignKey("lynxpay_users.id", ondelete="SET NULL"),
            )
        )
        batch.create_index("ix_lynxpay_api_keys_created_by_user_id", ["created_by_user_id"])

    op.create_index(
        "ix_lynxpay_audit_org_created",
        "lynxpay_audit_logs",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_lynxpay_callback_status_received",
        "lynxpay_mpesa_callbacks",
        ["processing_status", "received_at"],
    )
    op.create_index(
        "ix_lynxpay_webhook_endpoint_status_created",
        "lynxpay_webhook_endpoints",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_lynxpay_webhook_delivery_endpoint_status_created",
        "lynxpay_webhook_deliveries",
        ["webhook_endpoint_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_lynxpay_webhook_delivery_endpoint_status_created",
        table_name="lynxpay_webhook_deliveries",
    )
    op.drop_index(
        "ix_lynxpay_webhook_endpoint_status_created", table_name="lynxpay_webhook_endpoints"
    )
    op.drop_index("ix_lynxpay_callback_status_received", table_name="lynxpay_mpesa_callbacks")
    op.drop_index("ix_lynxpay_audit_org_created", table_name="lynxpay_audit_logs")

    with op.batch_alter_table("lynxpay_api_keys") as batch:
        batch.drop_index("ix_lynxpay_api_keys_created_by_user_id")
        batch.drop_column("created_by_user_id")

    with op.batch_alter_table("lynxpay_merchant_accounts") as batch:
        batch.drop_index("ix_lynxpay_merchant_approval_queue")
        batch.drop_column("suspended_at")
        batch.drop_column("rejection_reason")
        batch.drop_column("rejected_at")
        batch.drop_column("approved_by_user_id")
        batch.drop_column("approved_at")
        batch.drop_column("approval_submitted_at")
        batch.drop_constraint("ck_lynxpay_merchant_status", type_="check")
        batch.create_check_constraint(
            "ck_lynxpay_merchant_status",
            "status IN ('active','inactive','suspended','pending_setup','credentials_added','verified')",
        )

    op.drop_table("lynxpay_email_verification_tokens")

    with op.batch_alter_table("lynxpay_users") as batch:
        batch.drop_column("is_platform_admin")
        batch.drop_column("email_verified_at")

    with op.batch_alter_table("lynxpay_organizations") as batch:
        batch.drop_column("accepted_privacy_version")
        batch.drop_column("accepted_terms_version")
        batch.drop_column("privacy_accepted_at")
        batch.drop_column("terms_accepted_at")
