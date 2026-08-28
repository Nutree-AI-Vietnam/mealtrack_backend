import re

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_revision_graph_has_single_head() -> None:
    script_dir = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = script_dir.get_heads()
    current_head = script_dir.get_current_head()

    assert heads == [current_head]
    assert current_head is not None
    assert re.fullmatch(r"\d{3}|\d{14}|\d{20}", current_head)


def test_user_provider_migration_follows_current_head() -> None:
    script_dir = ScriptDirectory.from_config(Config("alembic.ini"))
    migration = script_dir.get_revision("20260828000001")

    assert migration is not None
    assert migration.down_revision == "20260827000001"
