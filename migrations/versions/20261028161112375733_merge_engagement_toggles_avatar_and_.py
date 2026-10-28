"""Merge the engagement-toggle, user-avatar and scan-feedback migration branches.

Revision ID: 20261028161112375733
Revises: 20261026102822211100, 20261026185557393061, 20261027154254949604
Create Date: 2026-10-28 16:11:12.472190

"""

from collections.abc import Sequence

revision: str = "20261028161112375733"
down_revision: tuple[str, str, str] = (
    "20261026102822211100",
    "20261026185557393061",
    "20261027154254949604",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record that all three independent schema branches are applied."""


def downgrade() -> None:
    """Keep the three parent branches available for explicit rollback."""
