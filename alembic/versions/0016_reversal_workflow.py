"""add controlled M-PESA reversal workflow

Revision ID: 0016_reversal_workflow
Revises: 0015_expand_tenant_rls
Create Date: 2026-07-23 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016_reversal_workflow"
down_revision: str | None = "0015_expand_tenant_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "lynxpay_reversal_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("merchant_account_id", sa.String(length=36), nullable=False),
        sa.Column("payment_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("idempotency_request_hash", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="KES"),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default="pending_approval"
        ),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("approved_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("originator_conversation_id", sa.String(length=160), nullable=True),
        sa.Column("conversation_id", sa.String(length=160), nullable=True),
        sa.Column("provider_transaction_id", sa.String(length=100), nullable=True),
        sa.Column("response_code", sa.String(length=30), nullable=True),
        sa.Column("response_description", sa.String(length=500), nullable=True),
        sa.Column("request_payload_redacted", JSON_TYPE, nullable=True),
        sa.Column("response_payload", JSON_TYPE, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submission_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_lynxpay_reversal_amount_positive"),
        sa.CheckConstraint("currency = 'KES'", name="ck_lynxpay_reversal_currency_kes"),
        sa.CheckConstraint(
            "status IN ('pending_approval','approved','submitting','submitted','succeeded','failed','timeout','unknown','cancelled')",
            name="ck_lynxpay_reversal_status",
        ),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["lynxpay_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["merchant_account_id"], ["lynxpay_merchant_accounts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["lynxpay_organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["payment_id"], ["lynxpay_payments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["lynxpay_users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "merchant_account_id",
            "idempotency_key",
            name="uq_lynxpay_reversal_merchant_idempotency",
        ),
        sa.UniqueConstraint("originator_conversation_id"),
    )
    for column in (
        "organization_id",
        "merchant_account_id",
        "payment_id",
        "status",
        "originator_conversation_id",
        "conversation_id",
        "lease_owner",
        "lease_expires_at",
    ):
        op.create_index(
            f"ix_lynxpay_reversal_requests_{column}",
            "lynxpay_reversal_requests",
            [column],
        )
    op.create_index(
        "ix_lynxpay_reversal_queue",
        "lynxpay_reversal_requests",
        ["status", "lease_expires_at", "created_at"],
    )

    op.create_table(
        "lynxpay_reversal_callbacks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("merchant_account_id", sa.String(length=36), nullable=False),
        sa.Column("reversal_request_id", sa.String(length=36), nullable=True),
        sa.Column("callback_type", sa.String(length=20), nullable=False),
        sa.Column("originator_conversation_id", sa.String(length=160), nullable=True),
        sa.Column("conversation_id", sa.String(length=160), nullable=True),
        sa.Column("result_code", sa.String(length=30), nullable=True),
        sa.Column("result_description", sa.String(length=500), nullable=True),
        sa.Column("transaction_id", sa.String(length=100), nullable=True),
        sa.Column("raw_payload", JSON_TYPE, nullable=False),
        sa.Column("raw_body", sa.Text(), nullable=False),
        sa.Column("processing_status", sa.String(length=40), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_ip", sa.String(length=45), nullable=True),
        sa.ForeignKeyConstraint(
            ["merchant_account_id"], ["lynxpay_merchant_accounts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["lynxpay_organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reversal_request_id"],
            ["lynxpay_reversal_requests.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "organization_id",
        "merchant_account_id",
        "reversal_request_id",
        "originator_conversation_id",
        "conversation_id",
        "processing_status",
        "received_at",
    ):
        op.create_index(
            f"ix_lynxpay_reversal_callbacks_{column}",
            "lynxpay_reversal_callbacks",
            [column],
        )

    if op.get_bind().dialect.name == "postgresql":
        for table in ("lynxpay_reversal_requests", "lynxpay_reversal_callbacks"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY lynxpay_tenant_isolation ON {table} "
                "USING (organization_id = current_setting('app.organization_id', true)) "
                "WITH CHECK (organization_id = current_setting('app.organization_id', true))"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in ("lynxpay_reversal_callbacks", "lynxpay_reversal_requests"):
            op.execute(f"DROP POLICY IF EXISTS lynxpay_tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("lynxpay_reversal_callbacks")
    op.drop_table("lynxpay_reversal_requests")
