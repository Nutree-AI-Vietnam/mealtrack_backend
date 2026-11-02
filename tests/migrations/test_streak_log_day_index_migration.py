"""The streak log-day index is additive and built without a write lock."""

from pathlib import Path

import src.infra.database.models  # noqa: F401
from src.infra.database.base import Base

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20261102140358147160_cover_streak_log_day_scan.py"
)


def _upgrade_and_downgrade() -> tuple[str, str]:
    upgrade, downgrade = MIGRATION_PATH.read_text().split("def downgrade() -> None:")
    return upgrade, downgrade


def test_migration_follows_the_supplement_review_head() -> None:
    text = MIGRATION_PATH.read_text()

    assert 'revision: str = "20261102140358147160"' in text
    assert 'down_revision: Union[str, None] = "20261101105647127919"' in text


def test_upgrade_builds_only_the_concurrent_partial_index() -> None:
    upgrade, _ = _upgrade_and_downgrade()

    assert "autocommit_block()" in upgrade
    assert "postgresql_concurrently=True" in upgrade
    assert "DROP INDEX CONCURRENTLY IF EXISTS ix_meal_streak_user_created" in upgrade
    assert "postgresql_include=" in upgrade
    assert "logged_on_day" in upgrade
    assert "status <> 'INACTIVE'" in upgrade
    assert "op.add_column" not in upgrade
    assert "op.drop_table" not in upgrade
    assert "op.alter_column" not in upgrade
    assert upgrade.count("op.create_index(") == 1


def test_downgrade_drops_the_index_concurrently() -> None:
    _, downgrade = _upgrade_and_downgrade()

    assert "autocommit_block()" in downgrade
    assert "postgresql_concurrently=True" in downgrade
    assert "ix_meal_streak_user_created" in downgrade
    assert "op.create_index" not in downgrade


def test_model_index_matches_the_migration() -> None:
    index = Base.metadata.tables["meal"].indexes
    streak = next(item for item in index if item.name == "ix_meal_streak_user_created")

    assert [column.name for column in streak.columns] == ["user_id", "created_at"]
    postgresql = streak.dialect_options["postgresql"]
    assert postgresql["include"] == ["logged_on_day", "status", "ready_at", "meal_id"]
    assert str(postgresql["where"]) == "status <> 'INACTIVE'"
