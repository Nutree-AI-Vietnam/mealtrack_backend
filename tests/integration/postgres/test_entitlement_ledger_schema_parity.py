"""The NM-680 migrations and ``Base.metadata.create_all`` build the same schema.

CI builds its database with ``create_all``; production runs the migration. Each
is built into its own throwaway schema (with a stub ``users`` table for the
foreign keys) and compared through the Postgres catalog: columns, defaults,
constraints and index definitions, including partial-index predicates. The
migrations' ``downgrade()`` must then remove all five tables.
"""

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

import src.infra.database.models  # noqa: F401
from src.infra.database.base import Base

pytestmark = pytest.mark.integration

# The ledger migration, then the D9 retention index, in revision order.
MIGRATIONS = (
    Path("migrations/versions/20261015184814225029_add_entitlement_ledger_tables.py"),
    Path(
        "migrations/versions/"
        "20261015233042477845_add_billing_event_raw_retention_index.py"
    ),
)
LEDGER_TABLES = (
    "billing_customer_identities",
    "billing_provider_events",
    "store_subscriptions",
    "entitlement_grants",
    "user_entitlements",
)


def _load_migrations() -> list:
    modules = []
    for index, path in enumerate(MIGRATIONS):
        spec = importlib.util.spec_from_file_location(f"nm680_migration_{index}", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


@pytest.fixture
def sync_engine(migrated_database: str) -> Iterator:
    engine = create_engine(
        migrated_database.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    )
    try:
        yield engine
    finally:
        engine.dispose()


def _enter_schema(connection: Connection, schema: str) -> None:
    connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    connection.execute(text(f'SET search_path TO "{schema}"'))
    connection.execute(text("CREATE TABLE users (id varchar(36) PRIMARY KEY)"))


def _describe(connection: Connection, schema: str) -> dict:
    connection.execute(text(f'SET search_path TO "{schema}"'))
    tables = list(LEDGER_TABLES)
    columns = connection.execute(
        text(
            """
            SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod),
                   a.attnotnull, pg_get_expr(d.adbin, d.adrelid)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE n.nspname = :schema AND c.relname = ANY(:tables)
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY 1, a.attnum
            """
        ),
        {"schema": schema, "tables": tables},
    ).all()
    constraints = connection.execute(
        text(
            """
            SELECT c.relname, con.conname, con.contype, pg_get_constraintdef(con.oid)
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = :schema AND c.relname = ANY(:tables)
            ORDER BY 1, 2
            """
        ),
        {"schema": schema, "tables": tables},
    ).all()
    indexes = connection.execute(
        text(
            """
            SELECT tablename, indexname, indexdef FROM pg_indexes
            WHERE schemaname = :schema AND tablename = ANY(:tables)
            ORDER BY 1, 2
            """
        ),
        {"schema": schema, "tables": tables},
    ).all()
    return {
        "columns": [tuple(row) for row in columns],
        "constraints": [tuple(row) for row in constraints],
        "indexes": [
            (table, name, definition.replace(f"{schema}.", ""))
            for table, name, definition in indexes
        ],
    }


def _table_names(connection: Connection, schema: str) -> set[str]:
    rows = connection.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = :schema"),
        {"schema": schema},
    ).scalars()
    return set(rows)


def test_migration_matches_create_all_and_downgrades_cleanly(sync_engine):
    suffix = uuid4().hex[:8]
    orm_schema, migration_schema = f"nm680_orm_{suffix}", f"nm680_mig_{suffix}"
    migrations = _load_migrations()
    try:
        with sync_engine.begin() as connection:
            _enter_schema(connection, orm_schema)
            Base.metadata.create_all(
                connection,
                tables=[Base.metadata.tables[name] for name in LEDGER_TABLES],
            )

            _enter_schema(connection, migration_schema)
            with Operations.context(MigrationContext.configure(connection)):
                for migration in migrations:
                    migration.upgrade()

            orm = _describe(connection, orm_schema)
            migrated = _describe(connection, migration_schema)
            assert {row[0] for row in migrated["columns"]} == set(LEDGER_TABLES)
            assert migrated["columns"] == orm["columns"]
            assert migrated["constraints"] == orm["constraints"]
            assert migrated["indexes"] == orm["indexes"]
            partial = {
                name: definition
                for _table, name, definition in migrated["indexes"]
                if " WHERE " in definition
            }
            assert set(partial) == {
                "uq_bci_one_primary",
                "ix_bpe_due",
                "ix_bpe_unmatched",
                "ix_bpe_lease_expiry",
                "ix_bpe_unmatched_app_user",
                "ix_bpe_raw_retention",
                "uq_eg_source",
                "ix_eg_user_live",
            }
            assert any(
                "'366 days'::interval" in definition
                for _t, name, _k, definition in migrated["constraints"]
                if name == "ck_eg_window_max"
            )

            connection.execute(text(f'SET search_path TO "{migration_schema}"'))
            with Operations.context(MigrationContext.configure(connection)):
                for migration in reversed(migrations):
                    migration.downgrade()
            assert _table_names(connection, migration_schema) == {"users"}
    finally:
        with sync_engine.begin() as connection:
            for schema in (orm_schema, migration_schema):
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def test_application_database_has_the_ledger_tables(sync_engine):
    with sync_engine.connect() as connection:
        assert set(LEDGER_TABLES) <= _table_names(connection, "public")
