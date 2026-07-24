"""add durable payment correlation identifiers

Revision ID: 0017_payment_correlation
Revises: 0016_reversal_workflow
Create Date: 2026-07-23 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_payment_correlation"
down_revision: str | None = "0016_reversal_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lynxpay_payments",
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
    )
    op.execute("UPDATE lynxpay_payments SET correlation_id = id WHERE correlation_id IS NULL")
    # The payment ledger guard is a deferred constraint trigger. Flush its no-op
    # events from this backfill before PostgreSQL attempts to alter the table.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.alter_column("lynxpay_payments", "correlation_id", nullable=False)
    op.create_index(
        "ix_lynxpay_payments_correlation_id",
        "lynxpay_payments",
        ["correlation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_lynxpay_payments_correlation_id",
        table_name="lynxpay_payments",
    )
    op.drop_column("lynxpay_payments", "correlation_id")
