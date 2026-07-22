"""add webhook endpoint health state

Revision ID: 0010_v2_webhook_endpoint_health
Revises: 0009_v2_submission_recovery
Create Date: 2026-07-16 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_v2_webhook_endpoint_health"
down_revision: str | None = "0009_v2_submission_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lynxpay_webhook_endpoints",
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "lynxpay_webhook_endpoints",
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "lynxpay_webhook_endpoints",
        sa.Column("pause_reason", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("lynxpay_webhook_endpoints", "pause_reason")
    op.drop_column("lynxpay_webhook_endpoints", "paused_at")
    op.drop_column("lynxpay_webhook_endpoints", "consecutive_failures")
