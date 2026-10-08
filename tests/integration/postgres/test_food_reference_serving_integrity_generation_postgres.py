"""PostgreSQL behaviour of the serving-size integrity trigger.

Every serving write still resets its reference to ``unknown`` and records an
event, but only writes a cached food search page could reflect bump the
catalog generation that flushes those pages.
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

pytestmark = pytest.mark.integration

MIGRATION_PATH = Path(
    "migrations/versions/"
    "20261007163030686049_narrow_food_reference_serving_integrity_.py"
)

_FUNCTION_IS_INSTALLED = text(
    "SELECT to_regprocedure('invalidate_food_reference_integrity_from_serving()')"
    " IS NOT NULL"
)


def _migration():
    spec = importlib.util.spec_from_file_location(
        "narrow_serving_integrity", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def database():
    url = os.getenv("TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+")):
        pytest.skip("TEST_DATABASE_URL must point to isolated PostgreSQL")
    url = url.replace("+asyncpg", "+psycopg2")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(url)
    schema = f"serving_integrity_{uuid.uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
        with engine.connect() as connection:
            connection.exec_driver_sql(f"SET search_path TO {schema}")
            _create_catalog_tables(connection)
            connection.commit()
            yield connection
            connection.rollback()
        with engine.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
    finally:
        engine.dispose()


def _create_catalog_tables(connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TABLE food_reference (
            id integer PRIMARY KEY,
            integrity_status varchar(16) NOT NULL DEFAULT 'unknown',
            integrity_policy_version varchar(64),
            integrity_checked_at timestamptz,
            integrity_reason varchar(64),
            integrity_input_digest varchar(64),
            created_at timestamptz DEFAULT now(),
            updated_at timestamptz DEFAULT now()
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE food_reference_serving_sizes (
            id serial PRIMARY KEY,
            food_reference_id integer NOT NULL REFERENCES food_reference(id),
            name varchar(64) NOT NULL,
            grams double precision NOT NULL
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE food_reference_integrity_control (
            id integer PRIMARY KEY,
            active_policy_version varchar(64) NOT NULL,
            catalog_integrity_generation bigint NOT NULL,
            updated_at timestamptz NOT NULL
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE food_reference_integrity_events (
            id varchar(36) PRIMARY KEY,
            food_reference_id integer NOT NULL REFERENCES food_reference(id),
            before_status varchar(16) NOT NULL,
            after_status varchar(16) NOT NULL,
            reason_code varchar(64) NOT NULL,
            policy_version varchar(64),
            actor_kind varchar(32) NOT NULL,
            created_at timestamptz NOT NULL
        )
        """
    )
    connection.exec_driver_sql(
        "INSERT INTO food_reference_integrity_control "
        "VALUES (1, 'nutrition_integrity_v1', 0, now())"
    )
    # Stand-in for the function the migration replaces; the trigger binds to
    # it by name, so replacing the function changes what the trigger runs.
    connection.exec_driver_sql(
        """
        CREATE FUNCTION invalidate_food_reference_integrity_from_serving()
        RETURNS trigger AS $$ BEGIN RETURN NULL; END; $$ LANGUAGE plpgsql
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER trg_food_reference_serving_integrity
        AFTER INSERT OR UPDATE OR DELETE ON food_reference_serving_sizes
        FOR EACH ROW
        EXECUTE FUNCTION invalidate_food_reference_integrity_from_serving()
        """
    )


def _run(database, action: str) -> None:
    with Operations.context(MigrationContext.configure(database)):
        getattr(_migration(), action)()
    database.commit()


def _generation(database) -> int:
    return database.execute(
        text(
            "SELECT catalog_integrity_generation "
            "FROM food_reference_integrity_control WHERE id = 1"
        )
    ).scalar_one()


def _add_reference(database, reference_id: int, *, servings: int) -> None:
    database.execute(
        text("INSERT INTO food_reference (id) VALUES (:id)"), {"id": reference_id}
    )
    _add_servings(database, reference_id, servings)


def _add_servings(database, reference_id: int, count: int) -> None:
    for index in range(count):
        database.execute(
            text(
                "INSERT INTO food_reference_serving_sizes "
                "(food_reference_id, name, grams) VALUES (:id, :name, :grams)"
            ),
            {"id": reference_id, "name": f"serving {index}", "grams": 100.0 + index},
        )


def _set_status(database, reference_id: int, status: str) -> None:
    database.execute(
        text("UPDATE food_reference SET integrity_status = :status WHERE id = :id"),
        {"id": reference_id, "status": status},
    )
    database.commit()


def _reference(database, reference_id: int) -> tuple[str, str | None]:
    return tuple(
        database.execute(
            text(
                "SELECT integrity_status, integrity_reason "
                "FROM food_reference WHERE id = :id"
            ),
            {"id": reference_id},
        ).one()
    )


def _event_before_statuses(database, reference_id: int) -> dict[str, int]:
    rows = database.execute(
        text(
            "SELECT before_status, count(*) FROM food_reference_integrity_events "
            "WHERE food_reference_id = :id AND reason_code = 'serving_changed' "
            "GROUP BY before_status"
        ),
        {"id": reference_id},
    ).all()
    return dict(rows)


def test_a_new_reference_with_servings_keeps_cached_pages(database):
    _run(database, "upgrade")

    _add_reference(database, 1, servings=2)
    database.commit()

    assert _generation(database) == 0
    assert _reference(database, 1) == ("unknown", "serving_changed")
    assert _event_before_statuses(database, 1) == {"unknown": 2}


def test_rewriting_a_shown_reference_flushes_once_per_transaction(database):
    _run(database, "upgrade")
    _add_reference(database, 1, servings=2)
    database.commit()
    _set_status(database, 1, "valid")

    database.execute(
        text("DELETE FROM food_reference_serving_sizes WHERE food_reference_id = 1")
    )
    _add_servings(database, 1, 3)
    database.commit()
    rewritten = _generation(database)
    database.execute(
        text(
            "UPDATE food_reference_serving_sizes SET grams = grams + 1 "
            "WHERE food_reference_id = 1"
        )
    )
    database.commit()

    assert rewritten == 1
    assert _generation(database) == 2
    assert _reference(database, 1) == ("unknown", "serving_changed")
    assert _event_before_statuses(database, 1) == {"unknown": 9, "valid": 1}


def test_each_shown_reference_in_a_transaction_flushes_once(database):
    _run(database, "upgrade")
    for reference_id in (1, 2):
        _add_reference(database, reference_id, servings=2)
    database.commit()

    database.execute(text("UPDATE food_reference_serving_sizes SET grams = grams + 1"))
    _add_reference(database, 3, servings=2)
    database.commit()

    assert _generation(database) == 2


def test_changes_to_a_quarantined_reference_keep_cached_pages(database):
    _run(database, "upgrade")
    _add_reference(database, 1, servings=2)
    database.commit()
    _set_status(database, 1, "quarantined")

    database.execute(
        text(
            "UPDATE food_reference_serving_sizes SET grams = grams + 1 "
            "WHERE food_reference_id = 1"
        )
    )
    database.commit()

    assert _generation(database) == 0
    assert _reference(database, 1) == ("unknown", "serving_changed")
    assert _event_before_statuses(database, 1) == {"quarantined": 1, "unknown": 3}


def test_downgrade_restores_a_flush_for_every_serving_write(database):
    _run(database, "upgrade")
    _run(database, "downgrade")

    _add_reference(database, 1, servings=2)
    database.commit()

    assert _generation(database) == 2
    assert _event_before_statuses(database, 1) == {"unknown": 2}


def test_databases_without_the_trigger_function_are_left_alone(database):
    database.exec_driver_sql(
        "DROP TRIGGER trg_food_reference_serving_integrity "
        "ON food_reference_serving_sizes"
    )
    database.exec_driver_sql(
        "DROP FUNCTION invalidate_food_reference_integrity_from_serving()"
    )
    database.commit()

    _run(database, "upgrade")
    _run(database, "downgrade")

    assert database.execute(_FUNCTION_IS_INSTALLED).scalar_one() is False
