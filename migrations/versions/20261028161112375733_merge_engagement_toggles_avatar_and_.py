"""Merge the engagement-toggle branch into delivery's head.

Delivery's head (meal logged-on-day) already joins the user-avatar and
scan-feedback branches.

Revision ID: 20261028161112375733
Revises: 20261028144132611127, 20261026185557393061
Create Date: 2026-10-28 16:11:12.472190

"""

from collections.abc import Sequence

revision: str = "20261028161112375733"
down_revision: tuple[str, str] = (
    "20261028144132611127",
    "20261026185557393061",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record that both schema branches are applied."""


def downgrade() -> None:
    """Keep both parent branches available for explicit rollback."""
