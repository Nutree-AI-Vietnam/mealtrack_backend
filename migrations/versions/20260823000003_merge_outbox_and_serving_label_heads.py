"""Merge the outbox and serving-label migration branches."""

from collections.abc import Sequence

revision: str = "20260823000003"
down_revision: tuple[str, str] = (
    "20260822000001",
    "20260823000002",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record that both independent schema branches are applied."""


def downgrade() -> None:
    """Keep the independent branches available for explicit rollback."""
