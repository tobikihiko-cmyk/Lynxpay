"""add merchant onboarding profile and payment purpose

Revision ID: 0005_merchant_onboarding
Revises: 0004_launch_hardening
Create Date: 2026-07-15 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_merchant_onboarding"
down_revision: str | None = "0004_launch_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("lynxpay_organizations") as batch:
        batch.add_column(sa.Column("business_type", sa.String(120), nullable=True))
        batch.add_column(sa.Column("county", sa.String(100), nullable=True))
        batch.add_column(sa.Column("town", sa.String(100), nullable=True))
        batch.add_column(sa.Column("support_email", sa.String(254), nullable=True))

    with op.batch_alter_table("lynxpay_payments") as batch:
        batch.add_column(
            sa.Column("purpose", sa.String(30), nullable=False, server_default="payment")
        )
        batch.create_check_constraint(
            "ck_lynxpay_payment_purpose",
            "purpose IN ('payment','merchant_verification')",
        )
        batch.create_index("ix_lynxpay_payments_purpose", ["purpose"])


def downgrade() -> None:
    with op.batch_alter_table("lynxpay_payments") as batch:
        batch.drop_index("ix_lynxpay_payments_purpose")
        batch.drop_constraint("ck_lynxpay_payment_purpose", type_="check")
        batch.drop_column("purpose")

    with op.batch_alter_table("lynxpay_organizations") as batch:
        batch.drop_column("support_email")
        batch.drop_column("town")
        batch.drop_column("county")
        batch.drop_column("business_type")
