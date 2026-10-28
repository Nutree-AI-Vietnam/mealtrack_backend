"""merge subscription access and scan feedback heads

Revision ID: 20261028084517715547
Revises: 20261026085224333692, 20261027154254949604
Create Date: 2026-10-28 08:45:18.259124

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "20261028084517715547"
down_revision: tuple[str, str] = (
    "20261026085224333692",
    "20261027154254949604",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record that the subscription access and scan feedback branches are applied."""


def downgrade() -> None:
    """Keep both parent branches available for explicit rollback."""
