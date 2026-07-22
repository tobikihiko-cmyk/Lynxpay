"""add RBAC session assurance

Revision ID: 0011_v2_rbac_mfa_sessions
Revises: 0010_v2_webhook_endpoint_health
Create Date: 2026-07-16 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_v2_rbac_mfa_sessions"
down_revision: str | None = "0010_v2_webhook_endpoint_health"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lynxpay_auth_sessions",
        sa.Column("mfa_authenticated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE lynxpay_users SET role = 'operator' WHERE role = 'member'")
    op.execute("UPDATE lynxpay_team_invitations SET role = 'operator' WHERE role = 'member'")


def downgrade() -> None:
    op.execute(
        "UPDATE lynxpay_users SET role = 'member' "
        "WHERE role IN ('operator','developer','support','accountant','read_only')"
    )
    op.execute(
        "UPDATE lynxpay_team_invitations SET role = 'member' "
        "WHERE role IN ('operator','developer','support','accountant','read_only')"
    )
    op.drop_column("lynxpay_auth_sessions", "mfa_authenticated_at")
