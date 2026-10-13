"""Merge the weekly-recap and open-wearables migration branches.

Revision ID: 20261013020801296576
Revises: 20261010181556723744, 20261012065247823604
Create Date: 2026-10-13 09:08:09.283884

"""

from collections.abc import Sequence

revision: str = "20261013020801296576"
down_revision: tuple[str, str] = (
    "20261010181556723744",
    "20261012065247823604",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record that both independent schema branches are applied."""


def downgrade() -> None:
    """Keep both parent branches available for explicit rollback."""
