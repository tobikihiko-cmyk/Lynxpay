"""create standalone LynxPay core

Revision ID: 0001_lynxpay_core
Revises: None
Create Date: 2026-07-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_lynxpay_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "lynxpay_organizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("legal_name", sa.String(250)),
        sa.Column("contact_email", sa.String(254), nullable=False),
        sa.Column("contact_phone", sa.String(20)),
        sa.Column("status", sa.String(30), nullable=False),
        *_timestamps(),
    )
    op.create_table(
        "lynxpay_users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("organization_id", "email", name="uq_lynxpay_user_org_email"),
    )
    op.create_index("ix_lynxpay_users_organization_id", "lynxpay_users", ["organization_id"])
    op.create_index("ix_lynxpay_users_email", "lynxpay_users", ["email"], unique=True)
    op.create_table(
        "lynxpay_merchant_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("merchant_name", sa.String(200), nullable=False),
        sa.Column("shortcode", sa.String(20), nullable=False),
        sa.Column("till_number", sa.String(20)),
        sa.Column("shortcode_type", sa.String(30), nullable=False),
        sa.Column("environment", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("callback_url", sa.String(500), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "organization_id", "shortcode", "environment", name="uq_lynxpay_org_shortcode_env"
        ),
        sa.CheckConstraint(
            "shortcode_type IN ('paybill','till','store_number','unknown')",
            name="ck_lynxpay_merchant_shortcode_type",
        ),
        sa.CheckConstraint(
            "environment IN ('sandbox','production')", name="ck_lynxpay_merchant_environment"
        ),
        sa.CheckConstraint(
            "status IN ('active','inactive','suspended','pending_setup')",
            name="ck_lynxpay_merchant_status",
        ),
    )
    op.create_index(
        "ix_lynxpay_merchant_accounts_organization_id",
        "lynxpay_merchant_accounts",
        ["organization_id"],
    )
    op.create_table(
        "lynxpay_daraja_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "merchant_account_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_merchant_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("consumer_key_encrypted", sa.Text(), nullable=False),
        sa.Column("consumer_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("passkey_encrypted", sa.Text(), nullable=False),
        sa.Column("shortcode", sa.String(20), nullable=False),
        sa.Column("initiator_name_encrypted", sa.Text()),
        sa.Column("security_credential_encrypted", sa.Text()),
        sa.Column("environment", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "environment IN ('sandbox','production')", name="ck_lynxpay_credential_environment"
        ),
    )
    op.create_index(
        "ix_lynxpay_daraja_credentials_merchant_account_id",
        "lynxpay_daraja_credentials",
        ["merchant_account_id"],
    )
    op.create_index(
        "uq_lynxpay_active_credential",
        "lynxpay_daraja_credentials",
        ["merchant_account_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        sqlite_where=sa.text("is_active = 1"),
    )
    op.create_table(
        "lynxpay_api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "merchant_account_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_merchant_accounts.id", ondelete="CASCADE"),
        ),
        sa.Column("key_prefix", sa.String(32), nullable=False, unique=True),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("scopes", JSON_TYPE, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_lynxpay_api_keys_organization_id", "lynxpay_api_keys", ["organization_id"])
    op.create_index(
        "ix_lynxpay_api_keys_merchant_account_id", "lynxpay_api_keys", ["merchant_account_id"]
    )
    op.create_index(
        "ix_lynxpay_api_keys_key_prefix", "lynxpay_api_keys", ["key_prefix"], unique=True
    )
    op.create_table(
        "lynxpay_payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "merchant_account_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_merchant_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("external_reference", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("idempotency_request_hash", sa.String(64)),
        sa.Column("order_id", sa.String(120)),
        sa.Column("invoice_id", sa.String(120)),
        sa.Column("customer_name", sa.String(200)),
        sa.Column("customer_phone", sa.String(15), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("callback_metadata", JSON_TYPE),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("checkout_request_id", sa.String(160)),
        sa.Column("merchant_request_id", sa.String(160)),
        sa.Column("mpesa_receipt_number", sa.String(100)),
        sa.Column("result_code", sa.String(30)),
        sa.Column("result_description", sa.String(500)),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint(
            "merchant_account_id", "external_reference", name="uq_lynxpay_merchant_external_ref"
        ),
        sa.UniqueConstraint(
            "merchant_account_id", "idempotency_key", name="uq_lynxpay_merchant_idempotency_key"
        ),
        sa.UniqueConstraint("checkout_request_id", name="uq_lynxpay_checkout_request_id"),
        sa.CheckConstraint("amount > 0", name="ck_lynxpay_payment_amount_positive"),
        sa.CheckConstraint(
            "status IN ('created','pending','stk_sent','success','failed','timeout','cancelled','reversed','unknown')",
            name="ck_lynxpay_payment_status",
        ),
    )
    op.create_index("ix_lynxpay_payments_organization_id", "lynxpay_payments", ["organization_id"])
    op.create_index(
        "ix_lynxpay_payments_merchant_account_id", "lynxpay_payments", ["merchant_account_id"]
    )
    op.create_index("ix_lynxpay_payments_status", "lynxpay_payments", ["status"])
    op.create_index(
        "ix_lynxpay_payments_checkout_request_id", "lynxpay_payments", ["checkout_request_id"]
    )
    op.create_index(
        "ix_lynxpay_payment_org_created", "lynxpay_payments", ["organization_id", "created_at"]
    )
    op.create_index(
        "ix_lynxpay_payment_merchant_created",
        "lynxpay_payments",
        ["merchant_account_id", "created_at"],
    )
    op.create_index(
        "uq_lynxpay_merchant_receipt",
        "lynxpay_payments",
        ["merchant_account_id", "mpesa_receipt_number"],
        unique=True,
        postgresql_where=sa.text("mpesa_receipt_number IS NOT NULL"),
        sqlite_where=sa.text("mpesa_receipt_number IS NOT NULL"),
    )
    op.create_table(
        "lynxpay_payment_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "payment_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_payments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "merchant_account_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_merchant_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("phone_number", sa.String(15), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("request_payload_redacted", JSON_TYPE, nullable=False),
        sa.Column("response_payload", JSON_TYPE),
        sa.Column("merchant_request_id", sa.String(160)),
        sa.Column("checkout_request_id", sa.String(160)),
        sa.Column("response_code", sa.String(30)),
        sa.Column("response_description", sa.String(500)),
        sa.Column("status", sa.String(30), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "payment_id", "attempt_number", name="uq_lynxpay_payment_attempt_number"
        ),
        sa.UniqueConstraint("checkout_request_id", name="uq_lynxpay_attempt_checkout_id"),
    )
    op.create_index(
        "ix_lynxpay_payment_attempts_payment_id", "lynxpay_payment_attempts", ["payment_id"]
    )
    op.create_index(
        "ix_lynxpay_payment_attempts_merchant_account_id",
        "lynxpay_payment_attempts",
        ["merchant_account_id"],
    )
    op.create_table(
        "lynxpay_mpesa_callbacks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "merchant_account_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_merchant_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "payment_id", sa.String(36), sa.ForeignKey("lynxpay_payments.id", ondelete="SET NULL")
        ),
        sa.Column("checkout_request_id", sa.String(160)),
        sa.Column("merchant_request_id", sa.String(160)),
        sa.Column("mpesa_receipt_number", sa.String(100)),
        sa.Column("result_code", sa.String(30)),
        sa.Column("result_description", sa.String(500)),
        sa.Column("raw_payload", JSON_TYPE, nullable=False),
        sa.Column("raw_body", sa.Text(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "duplicate_of_callback_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_mpesa_callbacks.id", ondelete="SET NULL"),
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_ip", sa.String(45)),
    )
    op.create_index(
        "ix_lynxpay_mpesa_callbacks_merchant_account_id",
        "lynxpay_mpesa_callbacks",
        ["merchant_account_id"],
    )
    op.create_index(
        "ix_lynxpay_mpesa_callbacks_payment_id", "lynxpay_mpesa_callbacks", ["payment_id"]
    )
    op.create_index(
        "ix_lynxpay_callback_merchant_received",
        "lynxpay_mpesa_callbacks",
        ["merchant_account_id", "received_at"],
    )
    op.create_index(
        "ix_lynxpay_callback_checkout_result",
        "lynxpay_mpesa_callbacks",
        ["checkout_request_id", "result_code"],
    )
    op.create_table(
        "lynxpay_webhook_endpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "merchant_account_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_merchant_accounts.id", ondelete="CASCADE"),
        ),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("event_types", JSON_TYPE, nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
    )
    op.create_index(
        "ix_lynxpay_webhook_endpoints_organization_id",
        "lynxpay_webhook_endpoints",
        ["organization_id"],
    )
    op.create_index(
        "ix_lynxpay_webhook_endpoints_merchant_account_id",
        "lynxpay_webhook_endpoints",
        ["merchant_account_id"],
    )
    op.create_table(
        "lynxpay_webhook_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "webhook_endpoint_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_webhook_endpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "payment_id", sa.String(36), sa.ForeignKey("lynxpay_payments.id", ondelete="SET NULL")
        ),
        sa.Column(
            "replay_of_delivery_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_webhook_deliveries.id", ondelete="SET NULL"),
        ),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", JSON_TYPE, nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("response_status_code", sa.Integer()),
        sa.Column("response_body", sa.Text()),
        *_timestamps(),
    )
    op.create_index(
        "ix_lynxpay_webhook_deliveries_webhook_endpoint_id",
        "lynxpay_webhook_deliveries",
        ["webhook_endpoint_id"],
    )
    op.create_index(
        "ix_lynxpay_webhook_deliveries_payment_id", "lynxpay_webhook_deliveries", ["payment_id"]
    )
    op.create_index(
        "ix_lynxpay_webhook_deliveries_event_type", "lynxpay_webhook_deliveries", ["event_type"]
    )
    op.create_index(
        "ix_lynxpay_webhook_deliveries_status", "lynxpay_webhook_deliveries", ["status"]
    )
    op.create_index(
        "ix_lynxpay_webhook_deliveries_next_retry_at",
        "lynxpay_webhook_deliveries",
        ["next_retry_at"],
    )
    op.create_table(
        "lynxpay_payment_ledger",
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
            sa.ForeignKey("lynxpay_payments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("status_from", sa.String(30)),
        sa.Column("status_to", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("details", JSON_TYPE),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_lynxpay_payment_ledger_organization_id", "lynxpay_payment_ledger", ["organization_id"]
    )
    op.create_index(
        "ix_lynxpay_payment_ledger_merchant_account_id",
        "lynxpay_payment_ledger",
        ["merchant_account_id"],
    )
    op.create_index(
        "ix_lynxpay_payment_ledger_payment_id", "lynxpay_payment_ledger", ["payment_id"]
    )
    op.create_index(
        "ix_lynxpay_payment_ledger_created_at", "lynxpay_payment_ledger", ["created_at"]
    )
    op.create_table(
        "lynxpay_audit_logs",
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
            sa.ForeignKey("lynxpay_merchant_accounts.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "actor_user_id", sa.String(36), sa.ForeignKey("lynxpay_users.id", ondelete="SET NULL")
        ),
        sa.Column(
            "actor_api_key_id",
            sa.String(36),
            sa.ForeignKey("lynxpay_api_keys.id", ondelete="SET NULL"),
        ),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(100), nullable=False),
        sa.Column("metadata", JSON_TYPE),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_lynxpay_audit_logs_organization_id", "lynxpay_audit_logs", ["organization_id"]
    )
    op.create_index(
        "ix_lynxpay_audit_logs_merchant_account_id", "lynxpay_audit_logs", ["merchant_account_id"]
    )
    op.create_index("ix_lynxpay_audit_logs_action", "lynxpay_audit_logs", ["action"])
    op.create_index("ix_lynxpay_audit_logs_created_at", "lynxpay_audit_logs", ["created_at"])


def downgrade() -> None:
    for table in (
        "lynxpay_audit_logs",
        "lynxpay_payment_ledger",
        "lynxpay_webhook_deliveries",
        "lynxpay_webhook_endpoints",
        "lynxpay_mpesa_callbacks",
        "lynxpay_payment_attempts",
        "lynxpay_payments",
        "lynxpay_api_keys",
        "lynxpay_daraja_credentials",
        "lynxpay_merchant_accounts",
        "lynxpay_users",
        "lynxpay_organizations",
    ):
        op.drop_table(table)
