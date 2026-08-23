from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations/versions/20260823000001_drop_food_reference_translation.py"
)


def test_drop_food_reference_translation_copies_vi_names_then_drops_table():
    text = MIGRATION.read_text()

    assert "UPDATE food_reference AS fr" in text
    assert "food_reference_translation AS src" in text
    assert "SET name_vi = src.name" in text
    assert 'op.drop_table("food_reference_translation")' in text


def test_drop_food_reference_translation_chains_from_current_head():
    text = MIGRATION.read_text()

    assert 'down_revision: str | None = "20260820000002"' in text
