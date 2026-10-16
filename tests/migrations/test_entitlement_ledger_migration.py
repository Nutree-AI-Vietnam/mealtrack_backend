"""Static checks on the NM-680 entitlement ledger migration (no database)."""

import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

MIGRATION = Path(
    "migrations/versions/20261015184814225029_add_entitlement_ledger_tables.py"
)
RETENTION_MIGRATION = Path(
    "migrations/versions/20261015233042477845_add_billing_event_raw_retention_index.py"
)
LEDGER_TABLES = {
    "billing_customer_identities",
    "billing_provider_events",
    "store_subscriptions",
    "entitlement_grants",
    "user_entitlements",
}


def _section(text: str, name: str) -> str:
    start = text.index(f"def {name}() -> None:")
    end = text.find("\ndef ", start + 1)
    return text[start : end if end != -1 else None]


def test_migration_is_the_single_head_after_chat_threads():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision("20261015184814225029")

    assert revision.down_revision == "20261015014451225604"
    assert len(script.get_heads()) == 1


def test_upgrade_only_creates_the_five_ledger_tables():
    upgrade = _section(MIGRATION.read_text(), "upgrade")

    created = set(re.findall(r'op\.create_table\(\s*"(\w+)"', upgrade))
    assert created == LEDGER_TABLES
    # Autogenerate drift on unrelated tables was trimmed out.
    for forbidden in ("drop_table", "alter_column", "drop_index", "drop_constraint"):
        assert f"op.{forbidden}" not in upgrade
    assert '"subscriptions"' not in upgrade


def test_downgrade_drops_exactly_the_five_ledger_tables():
    downgrade = _section(MIGRATION.read_text(), "downgrade")

    dropped = set(re.findall(r'op\.drop_table\("(\w+)"\)', downgrade))
    assert dropped == LEDGER_TABLES
    assert "op.create_table" not in downgrade


def test_every_named_constraint_and_partial_index_is_in_the_migration():
    text = MIGRATION.read_text()
    for name in (
        "uq_bci_provider_customer",
        "uq_bci_one_primary",
        "ck_bci_kind",
        "uq_bpe_event",
        "ck_bpe_status",
        "ix_bpe_due",
        "ix_bpe_unmatched",
        "ix_bpe_lease_expiry",
        "ix_bpe_unmatched_app_user",
        "uq_ss_key",
        "ck_ss_state",
        "ck_ss_environment",
        "uq_eg_source",
        "ix_eg_user_live",
        "ck_eg_window_max",
        "ck_eg_window_positive",
        "ck_eg_source",
        "ck_ue_state",
        "ix_ue_next_change",
    ):
        assert f'"{name}"' in text, name
    assert "interval '366 days'" in text


def test_retention_index_migration_follows_the_ledger_and_is_the_head():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision("20261015233042477845")

    assert revision.down_revision == "20261015184814225029"
    assert script.get_heads() == ["20261015233042477845"]


def test_retention_index_migration_only_touches_its_index():
    text = RETENTION_MIGRATION.read_text()
    upgrade, downgrade = _section(text, "upgrade"), _section(text, "downgrade")

    assert re.findall(r"op\.(\w+)\(", upgrade) == ["create_index"]
    assert re.findall(r"op\.(\w+)\(", downgrade) == ["drop_index"]
    for section in (upgrade, downgrade):
        assert '"ix_bpe_raw_retention"' in section
        assert 'sa.text("raw IS NOT NULL")' in section
