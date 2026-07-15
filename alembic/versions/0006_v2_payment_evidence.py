"""add Version 2 payment evidence and retry attempt metadata

Revision ID: 0006_v2_payment_evidence
Revises: 0005_merchant_onboarding
Create Date: 2026-07-16 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_v2_payment_evidence"
down_revision: str | None = "0005_merchant_onboarding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("lynxpay_payments") as batch:
        batch.add_column(
            sa.Column("success_source", sa.String(30), nullable=False, server_default="unknown")
        )
        batch.add_column(
            sa.Column("receipt_status", sa.String(30), nullable=False, server_default="missing")
        )
        batch.add_column(
            sa.Column("review_status", sa.String(30), nullable=False, server_default="none")
        )
        batch.add_column(sa.Column("review_reason", sa.String(500), nullable=True))
        batch.add_column(
            sa.Column(
                "provider_acceptance_state",
                sa.String(30),
                nullable=False,
                server_default="not_sent",
            )
        )
        batch.create_check_constraint(
            "ck_lynxpay_payment_success_source",
            "success_source IN ('callback','status_query','manual_review','unknown')",
        )
        batch.create_check_constraint(
            "ck_lynxpay_payment_receipt_status",
            "receipt_status IN ('present','missing','enriched_later','not_applicable')",
        )
        batch.create_check_constraint(
            "ck_lynxpay_payment_review_status",
            "review_status IN ('none','needs_review','resolved')",
        )
        batch.create_check_constraint(
            "ck_lynxpay_payment_provider_acceptance",
            "provider_acceptance_state IN ('not_sent','accepted','rejected','uncertain')",
        )
        batch.create_index(
            "ix_lynxpay_payment_merchant_status_created",
            ["merchant_account_id", "status", "created_at"],
        )
        batch.create_index(
            "ix_lynxpay_payment_merchant_review_created",
            ["merchant_account_id", "review_status", "created_at"],
        )
        batch.create_index("ix_lynxpay_payments_review_status", ["review_status"])

    with op.batch_alter_table("lynxpay_payment_attempts") as batch:
        batch.add_column(
            sa.Column("attempt_type", sa.String(20), nullable=False, server_default="initial")
        )
        batch.add_column(sa.Column("retry_reason", sa.String(500), nullable=True))
        batch.add_column(
            sa.Column(
                "initiated_by_user_id",
                sa.String(36),
                sa.ForeignKey("lynxpay_users.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "initiated_by_api_key_id",
                sa.String(36),
                sa.ForeignKey("lynxpay_api_keys.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch.create_index(
            "ix_lynxpay_payment_attempts_initiated_by_user_id", ["initiated_by_user_id"]
        )
        batch.create_index(
            "ix_lynxpay_payment_attempts_initiated_by_api_key_id", ["initiated_by_api_key_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("lynxpay_payment_attempts") as batch:
        batch.drop_index("ix_lynxpay_payment_attempts_initiated_by_api_key_id")
        batch.drop_index("ix_lynxpay_payment_attempts_initiated_by_user_id")
        batch.drop_column("initiated_by_api_key_id")
        batch.drop_column("initiated_by_user_id")
        batch.drop_column("retry_reason")
        batch.drop_column("attempt_type")

    with op.batch_alter_table("lynxpay_payments") as batch:
        batch.drop_index("ix_lynxpay_payments_review_status")
        batch.drop_index("ix_lynxpay_payment_merchant_review_created")
        batch.drop_index("ix_lynxpay_payment_merchant_status_created")
        batch.drop_constraint("ck_lynxpay_payment_provider_acceptance", type_="check")
        batch.drop_constraint("ck_lynxpay_payment_review_status", type_="check")
        batch.drop_constraint("ck_lynxpay_payment_receipt_status", type_="check")
        batch.drop_constraint("ck_lynxpay_payment_success_source", type_="check")
        batch.drop_column("provider_acceptance_state")
        batch.drop_column("review_reason")
        batch.drop_column("review_status")
        batch.drop_column("receipt_status")
        batch.drop_column("success_source")
