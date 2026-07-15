"""add Version 2 worker heartbeat

Revision ID: 0008_v2_worker_heartbeat
Revises: 0007_v2_onboarding_approval
Create Date: 2026-07-16 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_v2_worker_heartbeat"
down_revision: str | None = "0007_v2_onboarding_approval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "lynxpay_worker_heartbeats",
        sa.Column("worker_id", sa.String(100), primary_key=True),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", JSON_TYPE),
    )
    op.create_index(
        "ix_lynxpay_worker_heartbeats_last_seen_at",
        "lynxpay_worker_heartbeats",
        ["last_seen_at"],
    )


def downgrade() -> None:
    op.drop_table("lynxpay_worker_heartbeats")
