from pathlib import Path

import src.infra.database.models  # noqa: F401
from src.infra.database.base import Base

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260609000001_add_user_owner_foreign_keys.py"
)


def _user_id_foreign_key(table_name: str):
    column = Base.metadata.tables[table_name].c.user_id
    return next(fk for fk in column.foreign_keys if fk.column.table.name == "users")


def test_user_owned_models_reference_users_with_cascade() -> None:
    for table_name in (
        "notification_preferences",
        "user_fcm_tokens",
        "weekly_macro_budgets",
        "cheat_days",
        "meal",
        "saved_suggestions",
    ):
        foreign_key = _user_id_foreign_key(table_name)
        assert foreign_key.column.name == "id"
        assert foreign_key.ondelete == "CASCADE"


def test_saved_suggestions_user_id_uses_canonical_user_id_width() -> None:
    user_id = Base.metadata.tables["saved_suggestions"].c.user_id

    assert user_id.type.length == 36


def test_users_provider_column_fits_email_link() -> None:
    provider = Base.metadata.tables["users"].c.provider
    length = getattr(provider.type, "length", None)
    assert length is not None
    assert length >= len("EMAIL_LINK")


def test_migration_repairs_orphans_before_adding_constraints() -> None:
    migration_text = MIGRATION_PATH.read_text()

    repair_index = migration_text.index("UPDATE saved_suggestions AS ss")
    cleanup_index = migration_text.index("DELETE FROM saved_suggestions AS ss")
    alter_index = migration_text.index("op.alter_column(")
    constraint_index = migration_text.index("fk_saved_suggestions_user_id_users")

    assert "ss.user_id = users.firebase_uid" in migration_text
    assert repair_index < cleanup_index < alter_index < constraint_index
    assert "fk_weekly_macro_budgets_user_id_users" in migration_text
    assert "fk_cheat_days_user_id_users" in migration_text
