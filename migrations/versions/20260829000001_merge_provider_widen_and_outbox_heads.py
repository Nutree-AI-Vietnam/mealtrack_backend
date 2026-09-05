"""Merge delivery's provider-widen head with the outbox migration branch.

Delivery already stamped ``20260828000001`` (parent ``20260825000001``).
This branch's outbox path is a sibling of that revision, so a merge
revision is required to keep a single Alembic head.

Revision ID: 20260829000001
Revises: 20260827000001, 20260828000001
"""

from collections.abc import Sequence


revision: str = "20260829000001"
down_revision: tuple[str, str] = (
    "20260827000001",
    "20260828000001",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record that both schema branches are applied."""


def downgrade() -> None:
    """Keep both parent branches available for explicit rollback."""
