"""Add vacations.

Revision ID: 20261006000001
Revises: 20261005022758771114
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261006000001"
down_revision: str | None = "20261005022758771114"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vacations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ended_on", sa.Date(), nullable=True),
        sa.Column("frozen_calories", sa.Float(), nullable=False),
        sa.Column("frozen_protein", sa.Float(), nullable=False),
        sa.Column("frozen_carbs", sa.Float(), nullable=False),
        sa.Column("frozen_fat", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vacations_user_id", "vacations", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_vacations_user_id", table_name="vacations")
    op.drop_table("vacations")
