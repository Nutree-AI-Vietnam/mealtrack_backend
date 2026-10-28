from pathlib import Path

import src.infra.database.models  # noqa: F401
from src.infra.database.base import Base

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20261026102822211100_add_user_avatar_fields.py"
)
AVATAR_COLUMNS = (
    "avatar_kind",
    "avatar_image_id",
    "avatar_image_url",
    "avatar_character",
    "avatar_color",
)


def _upgrade_and_downgrade() -> tuple[str, str]:
    upgrade, downgrade = MIGRATION_PATH.read_text().split("def downgrade() -> None:")
    return upgrade, downgrade


def test_migration_follows_the_reminder_migration() -> None:
    text = MIGRATION_PATH.read_text()

    assert 'down_revision: str | None = "20261026102517824023"' in text


def test_migration_touches_only_the_avatar_columns_on_users() -> None:
    text = MIGRATION_PATH.read_text()

    assert "op.alter_column" not in text
    assert "op.drop_index" not in text
    assert "op.create_index" not in text
    assert "notification_preferences" not in text
    assert text.count("op.add_column(") == len(AVATAR_COLUMNS)


def test_upgrade_adds_columns_with_auto_default_and_checks() -> None:
    upgrade, _ = _upgrade_and_downgrade()

    for column_name in AVATAR_COLUMNS:
        assert f'"{column_name}"' in upgrade
    assert 'server_default="auto"' in upgrade
    assert "avatar_kind IN ('auto', 'photo', 'character', 'initials')" in upgrade
    assert "avatar_color IS NULL OR (avatar_color >= 0 AND avatar_color < 8)" in upgrade


def test_downgrade_drops_every_avatar_column_and_check() -> None:
    _, downgrade = _upgrade_and_downgrade()

    for column_name in AVATAR_COLUMNS:
        assert f'op.drop_column("users", "{column_name}")' in downgrade
    assert '"check_users_avatar_kind"' in downgrade
    assert '"check_users_avatar_color"' in downgrade


def test_model_matches_migration() -> None:
    users = Base.metadata.tables["users"]

    assert users.c.avatar_kind.nullable is False
    assert users.c.avatar_kind.server_default.arg == "auto"
    for column_name in AVATAR_COLUMNS[1:]:
        assert users.c[column_name].nullable is True
    check_names = {constraint.name for constraint in users.constraints}
    assert {"check_users_avatar_kind", "check_users_avatar_color"} <= check_names
