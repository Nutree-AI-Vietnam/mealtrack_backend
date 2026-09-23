"""The recipe-knowledge revision is the single Alembic head."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REVISION = "20260922114500000000"
MIGRATION = Path(
    "migrations/versions/20260922114500000000_evolve_recipe_knowledge_and_pantry.py"
)


def test_recipe_knowledge_revision_is_the_only_head() -> None:
    script_dir = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = script_dir.get_heads()

    assert heads == [REVISION]
    assert script_dir.get_current_head() == REVISION
    revision = script_dir.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == "20260921145751448341"


def test_revision_orders_ingredients_and_drops_pantry_stock_kind() -> None:
    text = MIGRATION.read_text()
    downgrade_at = text.index("def downgrade")
    helpers_at = text.index("def _upgrade_ingredients")
    upgrade = text[:downgrade_at] + text[helpers_at:]

    assert "uq_meal_catalog_ingredients_position" in upgrade
    assert '["catalog_meal_id", "position"]' in upgrade
    assert 'op.drop_column("weekly_meal_plan_pantry_items", "stock_kind")' in upgrade
    assert "available_amount" in upgrade
    assert "stock_kind IN ('bought', 'owned')" not in upgrade
    assert "recipe_payload" in upgrade
    assert "weekly_grocery_item_state" in upgrade
