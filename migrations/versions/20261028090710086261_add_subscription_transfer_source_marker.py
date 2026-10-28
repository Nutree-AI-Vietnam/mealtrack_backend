"""add subscription transfer source marker

Revision ID: 20261028090710086261
Revises: 20261028085152877654
Create Date: 2026-10-28 09:07:10.137724

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261028090710086261"
down_revision: str | None = "20261028085152877654"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remember a TRANSFER source whose RevenueCat customer could not be read.

    Autogenerate also reported the long-standing model/database drift noted
    in 20261026085224333692; none of it belongs to this change.
    """
    op.add_column(
        "subscriptions",
        sa.Column(
            "provider_transfer_source_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    """Remove the additive transfer source marker."""
    op.drop_column("subscriptions", "provider_transfer_source_at")
