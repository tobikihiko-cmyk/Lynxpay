"""enforce payment status and ledger coupling

Revision ID: 0012_v2_ledger_coupling
Revises: 0011_v2_rbac_mfa_sessions
Create Date: 2026-07-16 16:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_v2_ledger_coupling"
down_revision: str | None = "0011_v2_rbac_mfa_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE FUNCTION lynxpay_require_payment_ledger() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT EXISTS (
                SELECT 1 FROM lynxpay_payment_ledger l
                WHERE l.payment_id = NEW.id
                  AND l.status_from = OLD.status
                  AND l.status_to = NEW.status
                  AND l.created_at >= transaction_timestamp()
            ) THEN
                RAISE EXCEPTION 'Payment status change requires ledger evidence';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER lynxpay_payment_status_requires_ledger
        AFTER UPDATE ON lynxpay_payments
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION lynxpay_require_payment_ledger()
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS lynxpay_payment_status_requires_ledger ON lynxpay_payments")
    op.execute("DROP FUNCTION IF EXISTS lynxpay_require_payment_ledger()")
