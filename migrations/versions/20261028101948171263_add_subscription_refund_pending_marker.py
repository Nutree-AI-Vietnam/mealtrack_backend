"""add subscription refund pending marker

Revision ID: 20261028101948171263
Revises: 20261028090710086261
Create Date: 2026-10-28 10:19:48.209454

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261028101948171263"
down_revision: str | None = "20261028090710086261"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remember a refund whose RevenueCat customer could not be read or applied.

    Autogenerate also reported the long-standing model/database drift noted
    in 20261026085224333692; none of it belongs to this change.
    """
    op.add_column(
        "subscriptions",
        sa.Column(
            "provider_refund_pending_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    """Remove the additive refund marker."""
    op.drop_column("subscriptions", "provider_refund_pending_at")
