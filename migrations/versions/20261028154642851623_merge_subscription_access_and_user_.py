"""merge subscription access and user avatar heads

Revision ID: 20261028154642851623
Revises: 20261028142458697212, 20261026102822211100
Create Date: 2026-10-28 15:46:43.335646

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "20261028154642851623"
down_revision: tuple[str, str] = (
    "20261028142458697212",
    "20261026102822211100",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record that the subscription access and user avatar branches are applied."""


def downgrade() -> None:
    """Keep both parent branches available for explicit rollback."""
