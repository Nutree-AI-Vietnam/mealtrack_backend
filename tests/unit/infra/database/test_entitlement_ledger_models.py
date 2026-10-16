"""ORM declarations for the NM-680 ledger tables (CI builds its DB from them)."""

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, create_engine, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

import src.infra.database.models  # noqa: F401
from src.infra.database.base import Base

LEDGER_TABLES = {
    "billing_customer_identities": {
        "checks": {"ck_bci_kind"},
        "uniques": {"uq_bci_provider_customer"},
        "indexes": {"uq_bci_one_primary", "ix_bci_user"},
    },
    "billing_provider_events": {
        "checks": {"ck_bpe_status"},
        "uniques": {"uq_bpe_event"},
        "indexes": {
            "ix_bpe_due",
            "ix_bpe_unmatched",
            "ix_bpe_user",
            "ix_bpe_lease_expiry",
            "ix_bpe_unmatched_app_user",
            "ix_bpe_raw_retention",
        },
    },
    "store_subscriptions": {
        "checks": {"ck_ss_environment", "ck_ss_state"},
        "uniques": {"uq_ss_key"},
        "indexes": {"ix_ss_user", "ix_ss_customer", "ix_ss_expiry"},
    },
    "entitlement_grants": {
        "checks": {"ck_eg_source", "ck_eg_window_positive", "ck_eg_window_max"},
        "uniques": set(),
        "indexes": {"uq_eg_source", "ix_eg_user_live"},
    },
    "user_entitlements": {
        "checks": {"ck_ue_state", "ck_ue_primary_source"},
        "uniques": set(),
        "indexes": {"ix_ue_next_change"},
    },
}

PARTIAL_INDEXES = {
    "uq_bci_one_primary": "kind = 'primary' AND retired_at IS NULL",
    "ix_bpe_due": "status IN ('received', 'retry')",
    "ix_bpe_unmatched": "status = 'unmatched'",
    "ix_bpe_lease_expiry": "status = 'processing'",
    "ix_bpe_unmatched_app_user": "status = 'unmatched'",
    "ix_bpe_raw_retention": "raw IS NOT NULL",
    "uq_eg_source": "source_ref IS NOT NULL",
    "ix_eg_user_live": "revoked_at IS NULL",
}


def _named(table, kind):
    return {c.name for c in table.constraints if isinstance(c, kind)}


def test_every_constraint_and_index_is_declared_on_the_model():
    for name, expected in LEDGER_TABLES.items():
        table = Base.metadata.tables[name]
        assert _named(table, CheckConstraint) == expected["checks"], name
        assert _named(table, UniqueConstraint) == expected["uniques"], name
        assert {index.name for index in table.indexes} == expected["indexes"], name


def test_partial_indexes_compile_with_where_clause_on_postgres():
    indexes = {
        index.name: index
        for name in LEDGER_TABLES
        for index in Base.metadata.tables[name].indexes
    }
    for index_name, predicate in PARTIAL_INDEXES.items():
        index: Index = indexes[index_name]
        ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        assert f"WHERE {predicate}" in ddl, ddl
    assert indexes["uq_bci_one_primary"].unique
    assert indexes["uq_eg_source"].unique


def test_grant_window_check_is_postgres_only_and_present_there():
    table = Base.metadata.tables["entitlement_grants"]
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "ends_at <= starts_at + interval '366 days'" in ddl


def test_projection_primary_key_is_user_and_entitlement_key():
    table = Base.metadata.tables["user_entitlements"]
    assert [column.name for column in table.primary_key.columns] == [
        "user_id",
        "entitlement_key",
    ]


def test_sqlite_metadata_build_still_works():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    assert set(LEDGER_TABLES) <= set(inspect(engine).get_table_names())
