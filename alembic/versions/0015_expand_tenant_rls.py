"""expand tenant RLS to merchant, API key, catalog, and invoice records

Revision ID: 0015_expand_tenant_rls
Revises: 0014_catalog_invoice_line_items
Create Date: 2026-07-23 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015_expand_tenant_rls"
down_revision: str | None = "0014_catalog_invoice_line_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


POLICIES = {
    "lynxpay_merchant_accounts": (
        "organization_id = current_setting('app.organization_id', true) "
        "OR id = current_setting('app.merchant_id', true)"
    ),
    "lynxpay_api_keys": (
        "organization_id = current_setting('app.organization_id', true) "
        "OR key_prefix = current_setting('app.api_key_prefix', true)"
    ),
    "lynxpay_catalog_items": ("organization_id = current_setting('app.organization_id', true)"),
    "lynxpay_invoices": (
        "organization_id = current_setting('app.organization_id', true) "
        "OR public_id = current_setting('app.public_invoice_id', true)"
    ),
    "lynxpay_invoice_line_items": (
        "EXISTS (SELECT 1 FROM lynxpay_invoices i "
        "WHERE i.id = invoice_id "
        "AND (i.organization_id = current_setting('app.organization_id', true) "
        "OR i.public_id = current_setting('app.public_invoice_id', true)))"
    ),
}


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, expression in POLICIES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY lynxpay_tenant_isolation ON {table} "
            f"USING ({expression}) WITH CHECK ({expression})"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in reversed(POLICIES):
        op.execute(f"DROP POLICY IF EXISTS lynxpay_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
