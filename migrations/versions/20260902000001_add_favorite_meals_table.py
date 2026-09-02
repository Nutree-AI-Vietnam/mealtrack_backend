"""Add favorite_meals table.

Revision ID: 20260829000003
Revises: 20260829000002
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260829000003"
down_revision: Union[str, Sequence[str], None] = "20260829000002"

branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "favorite_meals",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("meal_id", sa.String(length=36), nullable=False),
        sa.Column(
            "favorited_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["meal_id"],
            ["meal.meal_id"],
            name="fk_favorite_meals_meal_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_favorite_meals_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "meal_id", name="pk_favorite_meals"),
    )
    op.create_index(
        "ix_favorite_meals_meal_id", "favorite_meals", ["meal_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_favorite_meals_meal_id", table_name="favorite_meals")
    op.drop_table("favorite_meals")
