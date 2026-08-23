from pathlib import Path

MIGRATION = Path(
    "migrations/versions/20260823000001_add_serving_label_translations.py"
)


def test_serving_label_translation_migration_is_additive():
    text = MIGRATION.read_text()

    assert 'revision: str = "20260823000001"' in text
    assert 'down_revision: str | None = "20260820000002"' in text
    assert 'sa.Column("description", sa.String(length=100), nullable=True)' in text
    assert 'sa.Column("name_vi", sa.String(length=100), nullable=True)' in text
    assert '"serving_phrase_translation"' in text
