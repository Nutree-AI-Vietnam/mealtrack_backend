from pathlib import Path

import src.infra.database.models  # noqa: F401
from src.infra.database.base import Base

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20261026102517824023_add_per_meal_and_hydration_reminder_.py"
)
MEAL_SWITCHES = (
    "breakfast_reminder_enabled",
    "lunch_reminder_enabled",
    "dinner_reminder_enabled",
)


def _upgrade_and_downgrade() -> tuple[str, str]:
    text = MIGRATION_PATH.read_text()
    upgrade, downgrade = text.split("def downgrade() -> None:")
    return upgrade, downgrade


def test_migration_touches_only_notification_preferences() -> None:
    text = MIGRATION_PATH.read_text()

    assert "op.alter_column" not in text
    assert "op.drop_index" not in text
    assert "op.create_index" not in text
    assert '"users"' not in text


def test_migration_adds_switches_and_water_time_with_server_defaults() -> None:
    upgrade, _ = _upgrade_and_downgrade()

    for column_name in MEAL_SWITCHES:
        assert f'"{column_name}"' in upgrade
    assert 'server_default="true"' in upgrade
    assert '"hydration_reminder_time_minutes"' in upgrade
    assert 'server_default="930"' in upgrade
    assert '"check_hydration_reminder_time"' in upgrade


def test_migration_backfills_each_switch_from_the_legacy_flag() -> None:
    upgrade, _ = _upgrade_and_downgrade()

    assert "UPDATE notification_preferences SET " in upgrade
    for column_name in MEAL_SWITCHES:
        assert f"{column_name} = meal_reminders_enabled" in upgrade
    assert upgrade.index("op.add_column") < upgrade.index("UPDATE")


def test_downgrade_drops_the_new_columns_and_check() -> None:
    _, downgrade = _upgrade_and_downgrade()

    assert '"check_hydration_reminder_time"' in downgrade
    assert '"hydration_reminder_time_minutes"' in downgrade
    assert "reversed(_MEAL_SWITCHES)" in downgrade
    assert downgrade.count("op.drop_column") == 2


def test_model_matches_migration_defaults() -> None:
    table = Base.metadata.tables["notification_preferences"]

    for column_name in MEAL_SWITCHES:
        column = table.c[column_name]
        assert column.nullable is False
        assert column.server_default.arg == "true"
    water = table.c.hydration_reminder_time_minutes
    assert water.nullable is False
    assert water.server_default.arg == "930"
    check_names = {constraint.name for constraint in table.constraints}
    assert "check_hydration_reminder_time" in check_names
