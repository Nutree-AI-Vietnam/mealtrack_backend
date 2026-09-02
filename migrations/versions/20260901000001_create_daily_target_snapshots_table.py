"""Create immutable daily_target_snapshots table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901000001"
down_revision: str | None = "20260829000002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_target_snapshots",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("adjusted_target_calories", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "date"),
    )


def downgrade() -> None:
    op.drop_table("daily_target_snapshots")
