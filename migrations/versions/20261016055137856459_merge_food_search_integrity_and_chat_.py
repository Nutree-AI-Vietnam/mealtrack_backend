"""Merge the food-search integrity and chat-thread migration branches.

Revision ID: 20261016055137856459
Revises: 20261007163030686049, 20261015014451225604
Create Date: 2026-10-16 12:51:38.707379

"""

from collections.abc import Sequence

revision: str = "20261016055137856459"
down_revision: tuple[str, str] = (
    "20261007163030686049",
    "20261015014451225604",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record that both independent schema branches are applied."""


def downgrade() -> None:
    """Keep both parent branches available for explicit rollback."""
