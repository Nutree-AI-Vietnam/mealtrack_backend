import importlib
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

LOCAL_SCRIPT = Path("scripts/development/local.sh")
USER_ENHANCEMENT_MIGRATION = Path(
    "migrations/versions/002_add_seasonings_and_user_enhancements.py"
)
USER_PROVIDER_MIGRATION = Path(
    "migrations/versions/20260828000001_widen_users_provider_column.py"
)
FOOD_ITEM_UUID_MIGRATION = Path(
    "migrations/versions/006_convert_food_item_id_to_uuid.py"
)
FITNESS_GOAL_MIGRATION = Path("migrations/versions/012_update_fitness_goal_enum.py")
MEAL_TYPE_REPAIR_MIGRATION = Path(
    "migrations/versions/016_fix_missing_meal_type_column.py"
)
MEAL_TRANSLATION_MIGRATION = Path(
    "migrations/versions/017_add_meal_translation_tables.py"
)
TRAINING_MINUTES_MIGRATION = Path(
    "migrations/versions/028_backfill_training_minutes.py"
)
TRAINING_LEVEL_MIGRATION = Path("migrations/versions/029_backfill_training_level.py")
LANGUAGE_CODE_MIGRATION = Path("migrations/versions/043_add_language_code_to_users.py")


class FakeInspector:
    def __init__(self, columns_by_table: dict[str, set[str]]) -> None:
        self.columns_by_table = columns_by_table

    def get_table_names(self) -> list[str]:
        return list(self.columns_by_table)

    def get_columns(self, table_name: str) -> list[dict[str, str]]:
        return [
            {"name": column_name} for column_name in self.columns_by_table[table_name]
        ]


def load_init_postgres_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    sys.modules.pop("scripts.init_postgres_db", None)
    return importlib.import_module("scripts.init_postgres_db")


def test_local_development_script_stops_after_failed_migration() -> None:
    text = LOCAL_SCRIPT.read_text()

    assert text.startswith("#!/bin/bash\nset -Eeuo pipefail\n")


def test_local_development_script_bootstraps_missing_pip() -> None:
    text = LOCAL_SCRIPT.read_text()

    assert 'VENV_PYTHON=".venv/bin/python"' in text
    assert '"$VENV_PYTHON" -m pip --version' in text
    assert '"$VENV_PYTHON" -m ensurepip --upgrade' in text
    assert '"$VENV_PYTHON" -m pip install' in text


def test_local_development_script_discovers_python_without_hardcoded_python3() -> None:
    text = LOCAL_SCRIPT.read_text()

    assert 'HOST_PYTHON="${PYTHON_BIN:-}"' in text
    assert "PYTHON_BIN=/path/to/python3.13" in text
    assert "for candidate in python3.13 python3 python" in text
    assert '"$HOST_PYTHON" -m venv .venv' in text
    assert "python3 -m venv" not in text
    assert "python3 scripts/init_postgres_db.py" not in text
    assert "python3 -m uvicorn" not in text


def test_user_provider_enum_is_created_before_column_uses_it() -> None:
    text = USER_ENHANCEMENT_MIGRATION.read_text()

    create_index = text.index("provider_enum.create")
    add_column_index = text.index("sa.Column('provider', provider_enum")
    assert create_index < add_column_index
    assert "checkfirst=True" in text


def test_user_provider_migration_widens_legacy_column_for_email_link_auth() -> None:
    text = USER_PROVIDER_MIGRATION.read_text()
    from src.infra.database.models.user.user import User

    assert '"users"' in text
    assert '"provider"' in text
    assert "existing_type=sa.String(length=6)" in text
    assert "type_=sa.String(length=32)" in text
    assert 'postgresql_using="provider::text"' in text
    assert User.__table__.c.provider.type.length == 32


def test_food_item_uuid_migration_uses_postgres_ddl() -> None:
    text = FOOD_ITEM_UUID_MIGRATION.read_text()

    assert "MODIFY COLUMN" not in text
    assert "UUID()" not in text
    assert "ALTER COLUMN id TYPE VARCHAR(36) USING id::text" in text
    assert "gen_random_uuid()::text" in text


def test_fitness_goal_enum_is_created_before_column_uses_it() -> None:
    text = FITNESS_GOAL_MIGRATION.read_text()

    create_index = text.index("fitness_goal_enum.create")
    alter_index = text.index("type_=fitness_goal_enum")
    assert create_index < alter_index
    assert "postgresql_using='fitness_goal::fitnessgoalenum'" in text


def test_meal_type_repair_migration_uses_postgres_catalog() -> None:
    text = MEAL_TYPE_REPAIR_MIGRATION.read_text()

    assert "SHOW COLUMNS" not in text
    assert "information_schema.columns" in text


def test_meal_translation_migration_uses_postgres_catalog() -> None:
    text = MEAL_TRANSLATION_MIGRATION.read_text()

    assert "SHOW TABLES" not in text
    assert "SHOW INDEX" not in text
    assert "information_schema.tables" in text
    assert "pg_indexes" in text


def test_training_backfills_use_postgres_boolean_predicates() -> None:
    minutes_text = TRAINING_MINUTES_MIGRATION.read_text()
    level_text = TRAINING_LEVEL_MIGRATION.read_text()

    assert "is_current = 1" not in minutes_text
    assert "is_current = 1" not in level_text
    assert "is_current IS TRUE" in minutes_text
    assert "is_current IS TRUE" in level_text


def test_language_backfill_uses_postgres_update_from() -> None:
    text = LANGUAGE_CODE_MIGRATION.read_text()

    assert "INNER JOIN" not in text
    assert "np.is_deleted = 0" not in text
    assert "FROM notification_preferences AS np" in text
    assert "np.is_deleted IS FALSE" in text


def test_migrations_do_not_use_mysql_only_ddl_or_catalog_helpers() -> None:
    forbidden_terms = (
        "DATABASE()",
        "SHOW TABLES",
        "SHOW COLUMNS",
        "SHOW INDEX",
        "MODIFY COLUMN",
        "UUID()",
        "mysql.ENUM",
        "INNER JOIN",
    )

    for path in Path("migrations/versions").glob("*.py"):
        text = path.read_text()
        for term in forbidden_terms:
            assert term not in text, f"{path} contains {term}"


def test_recovery_stamp_rejects_schema_missing_model_tables(monkeypatch) -> None:
    init_postgres_db = load_init_postgres_db(monkeypatch)
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.String()))
    sa.Table("meal_recommendations", metadata, sa.Column("id", sa.String()))

    with pytest.raises(RuntimeError, match="meal_recommendations"):
        init_postgres_db.validate_schema_matches_metadata(
            FakeInspector({"users": {"id"}}),
            metadata,
        )


def test_recovery_stamp_rejects_schema_missing_model_columns(monkeypatch) -> None:
    init_postgres_db = load_init_postgres_db(monkeypatch)
    metadata = sa.MetaData()
    sa.Table(
        "meal_recommendations",
        metadata,
        sa.Column("id", sa.String()),
        sa.Column("seen_at", sa.DateTime(timezone=True)),
    )

    with pytest.raises(RuntimeError, match="meal_recommendations.seen_at"):
        init_postgres_db.validate_schema_matches_metadata(
            FakeInspector({"meal_recommendations": {"id"}}),
            metadata,
        )


def test_bootstrap_seeds_integrity_control_row_idempotently(monkeypatch) -> None:
    init_postgres_db = load_init_postgres_db(monkeypatch)
    engine = sa.create_engine("sqlite:///:memory:")

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                CREATE TABLE food_reference_integrity_control (
                    id INTEGER PRIMARY KEY,
                    active_policy_version VARCHAR(64) NOT NULL,
                    catalog_integrity_generation BIGINT NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        init_postgres_db.ensure_integrity_control_row(connection)
        init_postgres_db.ensure_integrity_control_row(connection)
        row = connection.execute(
            sa.text(
                """
                SELECT id, active_policy_version, catalog_integrity_generation
                FROM food_reference_integrity_control
                """
            )
        ).one()

    assert row == (1, "nutrition_integrity_v1", 0)
