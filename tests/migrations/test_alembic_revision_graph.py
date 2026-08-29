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


def test_user_provider_migration_keeps_delivery_parent() -> None:
    script_dir = ScriptDirectory.from_config(Config("alembic.ini"))
    provider_widen = script_dir.get_revision("20260828000001")
    merge_heads = script_dir.get_revision("20260829000001")

    assert provider_widen is not None
    assert provider_widen.down_revision == "20260825000001"
    assert merge_heads is not None
    assert set(merge_heads.down_revision) == {
        "20260827000001",
        "20260828000001",
    }
