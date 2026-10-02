"""add weekly grocery day lines

Revision ID: 20261002103128257288
Revises: 20260929155720339163
Create Date: 2026-10-02 17:31:28.266229

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261002103128257288"
down_revision: str | None = "20260929155720339163"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "weekly_grocery_day_lines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("food_reference_id", sa.Integer(), nullable=False),
        sa.Column("day_index", sa.Integer(), nullable=False),
        sa.Column("needed_amount", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("covered", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "covered OR needed_amount IS NOT NULL",
            name="ck_weekly_grocery_day_line_present",
        ),
        sa.CheckConstraint(
            "day_index BETWEEN 0 AND 6",
            name="ck_weekly_grocery_day_line_day",
        ),
        sa.CheckConstraint(
            "needed_amount IS NULL OR needed_amount >= 0",
            name="ck_weekly_grocery_day_line_amount",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["weekly_meal_plans.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_weekly_grocery_day_line",
        "weekly_grocery_day_lines",
        ["plan_id", "food_reference_id", "day_index"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_weekly_grocery_day_line", table_name="weekly_grocery_day_lines")
    op.drop_table("weekly_grocery_day_lines")
