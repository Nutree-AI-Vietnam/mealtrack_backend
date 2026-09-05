"""Persist whether leftover calories auto-adjust daily targets."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829000002"
down_revision: str | None = "20260829000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "weekly_auto_adjust",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "weekly_auto_adjust")
