"""Create write-once weekly_recaps table.

Revision ID: 20261010181556723744
Revises: 20261006115245714931
Create Date: 2026-10-11 01:15:56.739772

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261010181556723744"
down_revision: str | None = "20261006115245714931"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "weekly_recaps",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column(
            "stats",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column(
            "payload",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "week_start"),
    )


def downgrade() -> None:
    op.drop_table("weekly_recaps")
