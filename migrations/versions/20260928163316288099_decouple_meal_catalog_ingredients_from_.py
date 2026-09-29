"""decouple meal catalog ingredients from food reference

Revision ID: 20260928163316288099
Revises: 20260922114500000000
Create Date: 2026-09-28 23:33:16.290116

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260928163316288099"
down_revision: str | None = "20260922114500000000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "meal_catalog_ingredients",
        "food_reference_id",
        existing_type=sa.INTEGER(),
        nullable=True,
    )
    op.drop_constraint(
        "meal_catalog_ingredients_food_reference_id_fkey",
        "meal_catalog_ingredients",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "meal_catalog_ingredients_food_reference_id_fkey",
        "meal_catalog_ingredients",
        "food_reference",
        ["food_reference_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "weekly_grocery_item_state_food_reference_id_fkey",
        "weekly_grocery_item_state",
        type_="foreignkey",
    )
    op.drop_constraint(
        "weekly_meal_plan_pantry_items_food_reference_id_fkey",
        "weekly_meal_plan_pantry_items",
        type_="foreignkey",
    )


def downgrade() -> None:
    op.create_foreign_key(
        "weekly_meal_plan_pantry_items_food_reference_id_fkey",
        "weekly_meal_plan_pantry_items",
        "food_reference",
        ["food_reference_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "weekly_grocery_item_state_food_reference_id_fkey",
        "weekly_grocery_item_state",
        "food_reference",
        ["food_reference_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "meal_catalog_ingredients_food_reference_id_fkey",
        "meal_catalog_ingredients",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "meal_catalog_ingredients_food_reference_id_fkey",
        "meal_catalog_ingredients",
        "food_reference",
        ["food_reference_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column(
        "meal_catalog_ingredients",
        "food_reference_id",
        existing_type=sa.INTEGER(),
        nullable=False,
    )
