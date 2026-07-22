"""add durable STK submission and callback-link evidence

Revision ID: 0009_v2_submission_recovery
Revises: 0008_v2_worker_heartbeat
Create Date: 2026-07-16 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_v2_submission_recovery"
down_revision: str | None = "0008_v2_worker_heartbeat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lynxpay_payment_attempts",
        sa.Column("submission_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "lynxpay_payment_attempts",
        sa.Column("provider_responded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "lynxpay_payment_attempts",
        sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_lynxpay_payment_attempts_status", "lynxpay_payment_attempts", ["status"])
    op.create_index(
        "ix_lynxpay_payment_attempts_submission_started_at",
        "lynxpay_payment_attempts",
        ["submission_started_at"],
    )
    with op.batch_alter_table("lynxpay_mpesa_callbacks") as batch:
        batch.add_column(sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("linked_by_user_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("link_reason", sa.String(500), nullable=True))
        batch.create_foreign_key(
            "fk_lynxpay_callback_linked_by_user",
            "lynxpay_users",
            ["linked_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.execute("UPDATE lynxpay_payment_attempts SET status = 'not_sent' WHERE status = 'created'")


def downgrade() -> None:
    with op.batch_alter_table("lynxpay_mpesa_callbacks") as batch:
        batch.drop_constraint("fk_lynxpay_callback_linked_by_user", type_="foreignkey")
        batch.drop_column("link_reason")
        batch.drop_column("linked_by_user_id")
        batch.drop_column("linked_at")
    op.drop_index(
        "ix_lynxpay_payment_attempts_submission_started_at",
        table_name="lynxpay_payment_attempts",
    )
    op.drop_index("ix_lynxpay_payment_attempts_status", table_name="lynxpay_payment_attempts")
    op.drop_column("lynxpay_payment_attempts", "abandoned_at")
    op.drop_column("lynxpay_payment_attempts", "provider_responded_at")
    op.drop_column("lynxpay_payment_attempts", "submission_started_at")
