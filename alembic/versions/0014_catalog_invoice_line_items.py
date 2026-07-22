"""add merchant catalog and invoice line items

Revision ID: 0014_catalog_invoice_line_items
Revises: 0013_invoices
Create Date: 2026-07-22 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_catalog_invoice_line_items"
down_revision: str | None = "0013_invoices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lynxpay_catalog_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("merchant_account_id", sa.String(length=36), nullable=False),
        sa.Column("item_type", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="KES"),
        sa.Column("sku", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("currency = 'KES'", name="ck_lynxpay_catalog_item_currency_kes"),
        sa.CheckConstraint(
            "item_type IN ('service','product')", name="ck_lynxpay_catalog_item_type"
        ),
        sa.CheckConstraint(
            "status IN ('active','archived')", name="ck_lynxpay_catalog_item_status"
        ),
        sa.CheckConstraint("unit_price > 0", name="ck_lynxpay_catalog_item_price_positive"),
        sa.ForeignKeyConstraint(
            ["merchant_account_id"], ["lynxpay_merchant_accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["lynxpay_organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "merchant_account_id", "name", name="uq_lynxpay_merchant_catalog_item_name"
        ),
    )
    op.create_index(
        "ix_lynxpay_catalog_items_organization_id", "lynxpay_catalog_items", ["organization_id"]
    )
    op.create_index(
        "ix_lynxpay_catalog_items_merchant_account_id",
        "lynxpay_catalog_items",
        ["merchant_account_id"],
    )
    op.create_index("ix_lynxpay_catalog_items_status", "lynxpay_catalog_items", ["status"])
    op.create_index(
        "ix_lynxpay_catalog_merchant_status_sort",
        "lynxpay_catalog_items",
        ["merchant_account_id", "status", "sort_order"],
    )

    op.create_table(
        "lynxpay_invoice_line_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("catalog_item_id", sa.String(length=36), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("item_type", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("quantity", sa.Numeric(10, 2), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=False),
        sa.CheckConstraint(
            "item_type IN ('service','product','custom')",
            name="ck_lynxpay_invoice_line_item_type",
        ),
        sa.CheckConstraint("line_total > 0", name="ck_lynxpay_invoice_line_total_positive"),
        sa.CheckConstraint("quantity > 0", name="ck_lynxpay_invoice_line_quantity_positive"),
        sa.CheckConstraint("unit_price > 0", name="ck_lynxpay_invoice_line_price_positive"),
        sa.ForeignKeyConstraint(
            ["catalog_item_id"], ["lynxpay_catalog_items.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["invoice_id"], ["lynxpay_invoices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_lynxpay_invoice_line_items_invoice_id",
        "lynxpay_invoice_line_items",
        ["invoice_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_lynxpay_invoice_line_items_invoice_id", table_name="lynxpay_invoice_line_items"
    )
    op.drop_table("lynxpay_invoice_line_items")
    op.drop_index("ix_lynxpay_catalog_merchant_status_sort", table_name="lynxpay_catalog_items")
    op.drop_index("ix_lynxpay_catalog_items_status", table_name="lynxpay_catalog_items")
    op.drop_index(
        "ix_lynxpay_catalog_items_merchant_account_id", table_name="lynxpay_catalog_items"
    )
    op.drop_index("ix_lynxpay_catalog_items_organization_id", table_name="lynxpay_catalog_items")
    op.drop_table("lynxpay_catalog_items")
