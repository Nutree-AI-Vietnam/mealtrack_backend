"""Merge the web-funnel and outbox migration branches."""

from collections.abc import Sequence


revision: str = "20260827000001"
down_revision: tuple[str, str] = (
    "20260825000001",
    "20260823000003",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record that both schema branches are applied."""


def downgrade() -> None:
    """Keep both parent branches available for explicit rollback."""
