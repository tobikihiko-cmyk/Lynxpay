"""launch hardening for merchant lifecycle, key environments, callbacks, and evidence

Revision ID: 0004_launch_hardening
Revises: 0003_identity_security
Create Date: 2026-07-15 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_launch_hardening"
down_revision: str | None = "0003_identity_security"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("lynxpay_merchant_accounts") as batch:
        batch.drop_constraint("ck_lynxpay_merchant_status", type_="check")
        batch.create_check_constraint(
            "ck_lynxpay_merchant_status",
            "status IN ('active','inactive','suspended','pending_setup','credentials_added','verified')",
        )

    op.add_column(
        "lynxpay_api_keys",
        sa.Column("environment", sa.String(20), nullable=False, server_default="sandbox"),
    )
    op.create_index("ix_lynxpay_api_keys_environment", "lynxpay_api_keys", ["environment"])

    op.add_column(
        "lynxpay_mpesa_callbacks", sa.Column("callback_amount", sa.Numeric(12, 2), nullable=True)
    )
    op.add_column(
        "lynxpay_mpesa_callbacks", sa.Column("callback_phone", sa.String(15), nullable=True)
    )
    op.add_column(
        "lynxpay_mpesa_callbacks",
        sa.Column("processing_status", sa.String(40), nullable=False, server_default="received"),
    )
    op.create_index(
        "ix_lynxpay_mpesa_callbacks_processing_status",
        "lynxpay_mpesa_callbacks",
        ["processing_status"],
    )
    op.add_column(
        "lynxpay_mfa_totp_credentials", sa.Column("last_used_step", sa.Integer(), nullable=True)
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION lynxpay_reject_evidence_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'LynxPay audit and ledger evidence is append-only';
            END;
            $$
            """
        )
        for table in ("lynxpay_payment_ledger", "lynxpay_audit_logs"):
            op.execute(
                f"CREATE TRIGGER lynxpay_evidence_immutable "
                f"BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION lynxpay_reject_evidence_mutation()"
            )
            op.execute(f"REVOKE UPDATE, DELETE ON {table} FROM PUBLIC")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in ("lynxpay_payment_ledger", "lynxpay_audit_logs"):
            op.execute(f"DROP TRIGGER IF EXISTS lynxpay_evidence_immutable ON {table}")
        op.execute("DROP FUNCTION IF EXISTS lynxpay_reject_evidence_mutation()")

    op.drop_column("lynxpay_mfa_totp_credentials", "last_used_step")
    op.drop_index(
        "ix_lynxpay_mpesa_callbacks_processing_status", table_name="lynxpay_mpesa_callbacks"
    )
    op.drop_column("lynxpay_mpesa_callbacks", "processing_status")
    op.drop_column("lynxpay_mpesa_callbacks", "callback_phone")
    op.drop_column("lynxpay_mpesa_callbacks", "callback_amount")
    op.drop_index("ix_lynxpay_api_keys_environment", table_name="lynxpay_api_keys")
    op.drop_column("lynxpay_api_keys", "environment")

    with op.batch_alter_table("lynxpay_merchant_accounts") as batch:
        batch.drop_constraint("ck_lynxpay_merchant_status", type_="check")
        batch.create_check_constraint(
            "ck_lynxpay_merchant_status",
            "status IN ('active','inactive','suspended','pending_setup')",
        )
