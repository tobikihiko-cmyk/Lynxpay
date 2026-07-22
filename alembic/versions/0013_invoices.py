"""add invoice payment links

Revision ID: 0013_invoices
Revises: 0012_v2_ledger_coupling
Create Date: 2026-07-22 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_invoices"
down_revision: str | None = "0012_v2_ledger_coupling"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lynxpay_invoices",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("merchant_account_id", sa.String(length=36), nullable=False),
        sa.Column("invoice_number", sa.String(length=80), nullable=False),
        sa.Column("public_id", sa.String(length=80), nullable=False),
        sa.Column("client_name", sa.String(length=200), nullable=False),
        sa.Column("client_phone", sa.String(length=15), nullable=True),
        sa.Column("client_email", sa.String(length=254), nullable=True),
        sa.Column("service_title", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="KES"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="sent"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_id", sa.String(length=36), nullable=True),
        sa.Column("merchant_display_name", sa.String(length=200), nullable=False),
        sa.Column("merchant_display_address", sa.String(length=300), nullable=True),
        sa.Column("merchant_display_email", sa.String(length=254), nullable=True),
        sa.Column("merchant_display_phone", sa.String(length=20), nullable=True),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_lynxpay_invoice_amount_positive"),
        sa.CheckConstraint("currency = 'KES'", name="ck_lynxpay_invoice_currency_kes"),
        sa.CheckConstraint(
            "status IN ('draft','sent','paid','void','expired')",
            name="ck_lynxpay_invoice_status",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_account_id"], ["lynxpay_merchant_accounts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["lynxpay_organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["payment_id"], ["lynxpay_payments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "merchant_account_id", "invoice_number", name="uq_lynxpay_merchant_invoice_number"
        ),
        sa.UniqueConstraint("public_id", name="uq_lynxpay_invoice_public_id"),
    )
    op.create_index("ix_lynxpay_invoices_organization_id", "lynxpay_invoices", ["organization_id"])
    op.create_index(
        "ix_lynxpay_invoices_merchant_account_id",
        "lynxpay_invoices",
        ["merchant_account_id"],
    )
    op.create_index("ix_lynxpay_invoices_public_id", "lynxpay_invoices", ["public_id"])
    op.create_index("ix_lynxpay_invoices_status", "lynxpay_invoices", ["status"])


def downgrade() -> None:
    op.drop_index("ix_lynxpay_invoices_status", table_name="lynxpay_invoices")
    op.drop_index("ix_lynxpay_invoices_public_id", table_name="lynxpay_invoices")
    op.drop_index("ix_lynxpay_invoices_merchant_account_id", table_name="lynxpay_invoices")
    op.drop_index("ix_lynxpay_invoices_organization_id", table_name="lynxpay_invoices")
    op.drop_table("lynxpay_invoices")
