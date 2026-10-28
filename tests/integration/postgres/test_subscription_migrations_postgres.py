"""PostgreSQL checks for the subscription migrations' hand-written SQL.

Each test builds the ``subscriptions`` shape a migration meets in an
isolated schema (search_path holds only that schema) and runs the
migration's own upgrade or downgrade through Alembic's Operations.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from src.infra.database.models.subscription import Subscription

pytestmark = pytest.mark.integration

VERSIONS = Path("migrations/versions")
REFUNDED_ENUM = "20261028085152877654_add_refunded_to_native_subscription_.py"

# Migration 007's native type, before 'refunded' existed.
NATIVE_STATUS_TABLE = """
CREATE TYPE subscription_status_enum AS ENUM
    ('active', 'expired', 'cancelled', 'billing_issue');
CREATE TABLE subscriptions (
    id varchar(36) PRIMARY KEY,
    status subscription_status_enum NOT NULL DEFAULT 'active'
);
"""
# What create_all builds from the model (non-native Enum).
VARCHAR_STATUS_TABLE = """
CREATE TABLE subscriptions (
    id varchar(36) PRIMARY KEY,
    status varchar(13) NOT NULL
);
"""


def _migration(filename: str):
    spec = importlib.util.spec_from_file_location(
        filename.removesuffix(".py"), VERSIONS / filename
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def schema_connection():
    """A connection whose search_path is one fresh schema, dropped afterwards."""
    url = os.getenv("TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+")):
        pytest.skip("TEST_DATABASE_URL must point to isolated PostgreSQL")
    url = url.replace("+asyncpg", "+psycopg2")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(url)
    schema = f"subscription_migration_{uuid.uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
        with engine.connect() as connection:
            connection.exec_driver_sql(f"SET search_path TO {schema}")
            connection.commit()
            yield connection
            connection.rollback()
        with engine.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
    finally:
        engine.dispose()


def _create(connection, ddl: str) -> None:
    connection.exec_driver_sql(ddl)
    # The migration under test may need autocommit, which a connection in a
    # transaction cannot switch to.
    connection.commit()


def _run(connection, filename: str, action: str) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        getattr(_migration(filename), action)()


def _status_values(connection) -> list[str]:
    return list(
        connection.scalars(
            text(
                "SELECT enumlabel FROM pg_enum "
                "WHERE enumtypid = to_regtype('subscription_status_enum') "
                "ORDER BY enumsortorder"
            )
        )
    )


def test_refunded_is_added_to_the_native_status_enum_007_created(
    schema_connection,
):
    _create(schema_connection, NATIVE_STATUS_TABLE)

    _run(schema_connection, REFUNDED_ENUM, "upgrade")
    _run(schema_connection, REFUNDED_ENUM, "upgrade")  # idempotent

    assert _status_values(schema_connection) == [
        "active",
        "expired",
        "cancelled",
        "billing_issue",
        "refunded",
    ]
    # The migration and the model agree on every status a writer may store.
    assert set(_status_values(schema_connection)) == set(
        Subscription.__table__.c.status.type.enums
    )
    schema_connection.execute(
        text("INSERT INTO subscriptions (id, status) VALUES ('s1', 'refunded')")
    )
    assert (
        schema_connection.scalar(text("SELECT status FROM subscriptions")) == "refunded"
    )


def test_refunded_migration_is_a_no_op_on_a_varchar_status_column(
    schema_connection,
):
    """A database built from the models has no such type: nothing to alter."""
    _create(schema_connection, VARCHAR_STATUS_TABLE)

    _run(schema_connection, REFUNDED_ENUM, "upgrade")

    assert (
        schema_connection.scalar(
            text("SELECT to_regtype('subscription_status_enum')::text")
        )
        is None
    )
    schema_connection.execute(
        text("INSERT INTO subscriptions (id, status) VALUES ('s1', 'refunded')")
    )


def test_refunded_migration_downgrade_keeps_the_value(schema_connection):
    """PostgreSQL cannot drop an enum value; rows may already hold it."""
    _create(schema_connection, NATIVE_STATUS_TABLE)
    _run(schema_connection, REFUNDED_ENUM, "upgrade")
    schema_connection.execute(
        text("INSERT INTO subscriptions (id, status) VALUES ('s1', 'refunded')")
    )
    schema_connection.commit()

    _run(schema_connection, REFUNDED_ENUM, "downgrade")

    assert "refunded" in _status_values(schema_connection)
    assert (
        schema_connection.scalar(text("SELECT status FROM subscriptions")) == "refunded"
    )
