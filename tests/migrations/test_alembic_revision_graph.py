import re

from alembic.config import Config
from alembic.script import ScriptDirectory
from migrations.cli import pending_upgrade_revisions


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


def test_pending_upgrade_across_merge_graph_from_web_funnel_head() -> None:
    """Status/dry-run must resolve pending revs when current sits under merges."""
    script_dir = ScriptDirectory.from_config(Config("alembic.ini"))
    head = script_dir.get_current_head()
    assert head is not None

    pending_ids = [
        rev.revision
        for rev in pending_upgrade_revisions(script_dir, "20260825000001", head)
    ]

    assert "20260822000001" in pending_ids  # sibling outbox branch
    assert "20260823000003" in pending_ids  # merge
    assert "20260827000001" in pending_ids  # merge
    assert "20260828000001" in pending_ids  # provider widen sibling
    assert "20260829000001" in pending_ids  # merge to single head
    assert head in pending_ids
    assert "20260825000001" not in pending_ids


def test_orphaned_staging_revision_is_ancestor_of_head() -> None:
    """Staging is stamped 20260913160551428459; upgrade must resolve that id."""
    script_dir = ScriptDirectory.from_config(Config("alembic.ini"))
    orphan = "20260913160551428459"
    head = script_dir.get_current_head()
    assert head is not None

    orphan_rev = script_dir.get_revision(orphan)
    assert orphan_rev is not None
    assert orphan_rev.down_revision == "20260904044230151265"

    next_revision = "20260915035556051174"
    pending_ids = [
        rev.revision
        for rev in reversed(pending_upgrade_revisions(script_dir, orphan, head))
    ]
    assert pending_ids[0] == next_revision
    assert pending_ids[-1] == head
    assert orphan not in pending_ids

    from_chat = [
        rev.revision
        for rev in reversed(
            pending_upgrade_revisions(script_dir, "20260904044230151265", head)
        )
    ]
    assert from_chat[0] == orphan
    assert from_chat[1] == next_revision
    assert from_chat[-1] == head


def test_weekly_recaps_and_open_wearables_merge_to_one_head() -> None:
    """Sibling branches from the same parent must not leave two heads."""
    script_dir = ScriptDirectory.from_config(Config("alembic.ini"))
    parent = "20261006115245714931"
    weekly = "20261010181556723744"
    wearables = "20261012065247823604"
    head = script_dir.get_current_head()
    assert head is not None
    assert head not in {weekly, wearables}

    weekly_rev = script_dir.get_revision(weekly)
    wearables_rev = script_dir.get_revision(wearables)
    assert weekly_rev is not None and wearables_rev is not None
    assert weekly_rev.down_revision == parent
    assert wearables_rev.down_revision == parent

    pending_ids = [
        rev.revision
        for rev in reversed(pending_upgrade_revisions(script_dir, parent, head))
    ]
    assert weekly in pending_ids
    assert wearables in pending_ids
    assert pending_ids[-1] == head
