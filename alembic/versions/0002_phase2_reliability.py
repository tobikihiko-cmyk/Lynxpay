"""add phase 2 reliability, reconciliation, teams, encryption versions, and RLS

Revision ID: 0002_phase2_reliability
Revises: 0001_lynxpay_core
Create Date: 2026-07-15 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_phase2_reliability"
down_revision: str | None = "0001_lynxpay_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


RLS_POLICIES = {
    "lynxpay_payments": "organization_id = current_setting('app.organization_id', true)",
    "lynxpay_payment_status_checks": "organization_id = current_setting('app.organization_id', true)",
    "lynxpay_webhook_endpoints": "organization_id = current_setting('app.organization_id', true)",
    "lynxpay_payment_ledger": "organization_id = current_setting('app.organization_id', true)",
    "lynxpay_audit_logs": "organization_id = current_setting('app.organization_id', true)",
    "lynxpay_daraja_credentials": (
        "EXISTS (SELECT 1 FROM lynxpay_merchant_accounts m "
        "WHERE m.id = merchant_account_id "
        "AND m.organization_id = current_setting('app.organization_id', true))"
    ),
    "lynxpay_payment_attempts": (
        "EXISTS (SELECT 1 FROM lynxpay_merchant_accounts m "
        "WHERE m.id = merchant_account_id "
        "AND m.organization_id = current_setting('app.organization_id', true))"
    ),
    "lynxpay_mpesa_callbacks": (
        "EXISTS (SELECT 1 FROM lynxpay_merchant_accounts m "
        "WHERE m.id = merchant_account_id "
        "AND m.organization_id = current_setting('app.organization_id', true))"
    ),
    "lynxpay_webhook_deliveries": (
        "EXISTS (SELECT 1 FROM lynxpay_webhook_endpoints e "
        "WHERE e.id = webhook_endpoint_id "
        "AND e.organization_id = current_setting('app.organization_id', true))"
    ),
    "lynxpay_webhook_delivery_attempts": (
        "EXISTS (SELECT 1 FROM lynxpay_webhook_deliveries d "
        "JOIN lynxpay_webhook_endpoints e ON e.id = d.webhook_endpoint_id "
        "WHERE d.id = webhook_delivery_id "
        "AND e.organization_id = current_setting('app.organization_id', true))"
    ),
}


def upgrade() -> None:
    op.create_table(
        "lynxpay_team_invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "invited_by_user_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_users.id", ondelete="SET NULL"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_lynxpay_team_invitations_organization_id",
        "lynxpay_team_invitations",
        ["organization_id"],
    )
    op.create_index("ix_lynxpay_team_invitations_email", "lynxpay_team_invitations", ["email"])
    op.create_index(
        "uq_lynxpay_pending_invite_org_email",
        "lynxpay_team_invitations",
        ["organization_id", "email"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )

    op.add_column(
        "lynxpay_daraja_credentials",
        sa.Column("encryption_key_version", sa.String(50), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "lynxpay_webhook_endpoints",
        sa.Column("encryption_key_version", sa.String(50), nullable=False, server_default="legacy"),
    )

    for name, column in (
        (
            "reconciliation_attempts",
            sa.Column("reconciliation_attempts", sa.Integer(), nullable=False, server_default="0"),
        ),
        ("last_reconciled_at", sa.Column("last_reconciled_at", sa.DateTime(timezone=True))),
        ("next_reconciliation_at", sa.Column("next_reconciliation_at", sa.DateTime(timezone=True))),
        ("reconciliation_lease_owner", sa.Column("reconciliation_lease_owner", sa.String(100))),
        (
            "reconciliation_lease_expires_at",
            sa.Column("reconciliation_lease_expires_at", sa.DateTime(timezone=True)),
        ),
    ):
        del name
        op.add_column("lynxpay_payments", column)
    for column in (
        "next_reconciliation_at",
        "reconciliation_lease_owner",
        "reconciliation_lease_expires_at",
    ):
        op.create_index(f"ix_lynxpay_payments_{column}", "lynxpay_payments", [column])

    op.create_table(
        "lynxpay_payment_status_checks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "merchant_account_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_merchant_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "payment_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_payments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("checkout_request_id", sa.String(160), nullable=False),
        sa.Column("result_code", sa.String(30)),
        sa.Column("result_description", sa.String(500)),
        sa.Column("raw_response", JSON_TYPE, nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("organization_id", "merchant_account_id", "payment_id", "checked_at"):
        op.create_index(
            f"ix_lynxpay_payment_status_checks_{column}", "lynxpay_payment_status_checks", [column]
        )

    webhook_columns = (
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("last_error", sa.Text()),
        sa.Column("lease_owner", sa.String(100)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
    )
    for column in webhook_columns:
        op.add_column("lynxpay_webhook_deliveries", column)
    op.create_index(
        "ix_lynxpay_webhook_deliveries_lease_owner", "lynxpay_webhook_deliveries", ["lease_owner"]
    )
    op.create_index(
        "ix_lynxpay_webhook_deliveries_lease_expires_at",
        "lynxpay_webhook_deliveries",
        ["lease_expires_at"],
    )
    op.create_table(
        "lynxpay_webhook_delivery_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "webhook_delivery_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_webhook_deliveries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("response_status_code", sa.Integer()),
        sa.Column("response_body", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "webhook_delivery_id",
            "attempt_number",
            name="uq_lynxpay_webhook_delivery_attempt_number",
        ),
    )
    op.create_index(
        "ix_lynxpay_webhook_delivery_attempts_webhook_delivery_id",
        "lynxpay_webhook_delivery_attempts",
        ["webhook_delivery_id"],
    )

    if op.get_bind().dialect.name == "postgresql":
        for table, expression in RLS_POLICIES.items():
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY lynxpay_tenant_isolation ON {table} "
                f"USING ({expression}) WITH CHECK ({expression})"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in reversed(RLS_POLICIES):
            op.execute(f"DROP POLICY IF EXISTS lynxpay_tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("lynxpay_webhook_delivery_attempts")
    op.drop_index(
        "ix_lynxpay_webhook_deliveries_lease_expires_at", table_name="lynxpay_webhook_deliveries"
    )
    op.drop_index(
        "ix_lynxpay_webhook_deliveries_lease_owner", table_name="lynxpay_webhook_deliveries"
    )
    for column in ("delivered_at", "lease_expires_at", "lease_owner", "last_error", "max_attempts"):
        op.drop_column("lynxpay_webhook_deliveries", column)
    op.drop_table("lynxpay_payment_status_checks")
    for column in (
        "reconciliation_lease_expires_at",
        "reconciliation_lease_owner",
        "next_reconciliation_at",
    ):
        op.drop_index(f"ix_lynxpay_payments_{column}", table_name="lynxpay_payments")
    for column in (
        "reconciliation_lease_expires_at",
        "reconciliation_lease_owner",
        "next_reconciliation_at",
        "last_reconciled_at",
        "reconciliation_attempts",
    ):
        op.drop_column("lynxpay_payments", column)
    op.drop_column("lynxpay_webhook_endpoints", "encryption_key_version")
    op.drop_column("lynxpay_daraja_credentials", "encryption_key_version")
    op.drop_table("lynxpay_team_invitations")
