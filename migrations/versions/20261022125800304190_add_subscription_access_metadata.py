"""add subscription access metadata

Revision ID: 20261022125800304190
Revises: 20261013115453616929
Create Date: 2026-10-22 19:58:00.318205

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261022125800304190"
down_revision: str | None = "20261013115453616929"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add normalized provider facts used by the shared access policy."""
    op.add_column(
        "subscriptions",
        sa.Column(
            "provider",
            sa.String(length=32),
            server_default=sa.text("'revenuecat'"),
            nullable=False,
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column("grace_period_expires_at", sa.DateTime(timezone=True)),
    )
    op.add_column("subscriptions", sa.Column("period_type", sa.String(length=16)))
    op.add_column(
        "subscriptions",
        sa.Column("provider_snapshot_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    """Remove the additive provider facts."""
    op.drop_column("subscriptions", "provider_snapshot_at")
    op.drop_column("subscriptions", "period_type")
    op.drop_column("subscriptions", "grace_period_expires_at")
    op.drop_column("subscriptions", "provider")
