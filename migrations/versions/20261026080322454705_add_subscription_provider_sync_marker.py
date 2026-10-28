"""add subscription provider sync marker

Revision ID: 20261026080322454705
Revises: 20261022125800304190
Create Date: 2026-10-26 08:03:22.467763

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261026080322454705"
down_revision: str | None = "20261022125800304190"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Mark rows changed by a provider event whose snapshot could not be read."""
    op.add_column(
        "subscriptions",
        sa.Column(
            "provider_sync_required_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    """Remove the additive provider sync marker."""
    op.drop_column("subscriptions", "provider_sync_required_at")
