"""add_performance_fk_and_search_indexes

Revision ID: 20260915035556051174
Revises: 20260904044230151265
Create Date: 2026-09-15 10:55:56.356762

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260915035556051174"
down_revision: str | None = "20260904044230151265"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Foreign key indexes to eliminate full table scans during selectinload & correlated subqueries
    op.create_index(
        "ix_nutrition_meal_id",
        "nutrition",
        ["meal_id"],
        unique=False,
    )
    op.create_index(
        "ix_food_item_nutrition_id",
        "food_item",
        ["nutrition_id"],
        unique=False,
    )
    op.create_index(
        "ix_food_item_food_reference_id",
        "food_item",
        ["food_reference_id"],
        unique=False,
    )
    op.create_index(
        "ix_food_item_translation_meal_translation_id",
        "food_item_translation",
        ["meal_translation_id"],
        unique=False,
    )

    # 2. GIN trigram index on food_reference.name for ilike('%query%') matching
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_food_reference_name_trgm "
        "ON food_reference USING gin (name gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_food_reference_name_trgm")
    op.drop_index(
        "ix_food_item_translation_meal_translation_id",
        table_name="food_item_translation",
    )
    op.drop_index("ix_food_item_food_reference_id", table_name="food_item")
    op.drop_index("ix_food_item_nutrition_id", table_name="food_item")
    op.drop_index("ix_nutrition_meal_id", table_name="nutrition")
