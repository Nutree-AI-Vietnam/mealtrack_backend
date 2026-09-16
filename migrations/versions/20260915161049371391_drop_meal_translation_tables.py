"""drop_meal_translation_tables

Revision ID: 20260915161049371391
Revises: 20260915035556051174
Create Date: 2026-09-15 23:10:50.769924

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260915161049371391"
down_revision: str | None = "20260915035556051174"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        op.f("ix_food_item_translation_food_item_id"),
        table_name="food_item_translation",
    )
    op.drop_index(
        op.f("ix_food_item_translation_meal_translation_id"),
        table_name="food_item_translation",
    )
    op.drop_table("food_item_translation")
    op.drop_index(op.f("ix_meal_translation_meal_id"), table_name="meal_translation")
    op.drop_table("meal_translation")


def downgrade() -> None:
    op.create_table(
        "meal_translation",
        sa.Column("id", sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column("meal_id", sa.VARCHAR(length=36), autoincrement=False, nullable=True),
        sa.Column(
            "language", sa.VARCHAR(length=7), autoincrement=False, nullable=False
        ),
        sa.Column(
            "dish_name", sa.VARCHAR(length=255), autoincrement=False, nullable=False
        ),
        sa.Column(
            "translated_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "is_deleted",
            sa.BOOLEAN(),
            server_default=sa.text("false"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "meal_instruction",
            postgresql.JSON(astext_type=sa.Text()),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "meal_ingredients",
            postgresql.JSON(astext_type=sa.Text()),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "translation_version", sa.INTEGER(), autoincrement=False, nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["meal_id"], ["meal.meal_id"], name=op.f("meal_translation_meal_id_fkey")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("meal_translation_pkey")),
    )
    op.create_index(
        op.f("ix_meal_translation_meal_id"),
        "meal_translation",
        ["meal_id"],
        unique=False,
    )

    op.create_table(
        "food_item_translation",
        sa.Column("id", sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column(
            "meal_translation_id", sa.INTEGER(), autoincrement=False, nullable=False
        ),
        sa.Column(
            "food_item_id", sa.VARCHAR(length=36), autoincrement=False, nullable=False
        ),
        sa.Column("name", sa.VARCHAR(length=255), autoincrement=False, nullable=False),
        sa.Column("description", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column(
            "is_deleted",
            sa.BOOLEAN(),
            server_default=sa.text("false"),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["meal_translation_id"],
            ["meal_translation.id"],
            name=op.f("food_item_translation_meal_translation_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("food_item_translation_pkey")),
    )
    op.create_index(
        op.f("ix_food_item_translation_meal_translation_id"),
        "food_item_translation",
        ["meal_translation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_food_item_translation_food_item_id"),
        "food_item_translation",
        ["food_item_id"],
        unique=False,
    )
