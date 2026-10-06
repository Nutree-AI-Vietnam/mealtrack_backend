"""PostgreSQL regression for late two-slot weekly plans."""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from src.domain.model.weekly_meal_planner import (
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanStatus,
)
from src.infra.database.models import WeeklyPlanLateLegacySlotRepairORM

pytestmark = pytest.mark.integration

MIGRATION_PATH = Path(
    "migrations/versions/"
    "20261006115245714931_repair_late_legacy_two_slot_weekly_plans.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("late_legacy_repair", MIGRATION_PATH)
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
    schema = f"late_repair_{uuid.uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
        with engine.connect() as connection:
            connection.exec_driver_sql(f"SET search_path TO {schema}, public")
            connection.exec_driver_sql(
                """
                CREATE TABLE weekly_meal_plans (
                    id varchar(36) PRIMARY KEY,
                    algorithm_version varchar(32) NOT NULL,
                    status varchar(16) NOT NULL,
                    revision integer NOT NULL,
                    updated_at timestamptz NOT NULL
                )
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TABLE weekly_meal_plan_slots (
                    id varchar(36) PRIMARY KEY,
                    plan_id varchar(36) NOT NULL REFERENCES weekly_meal_plans(id),
                    day_index integer NOT NULL CHECK (day_index BETWEEN 0 AND 6),
                    slot_index integer NOT NULL CHECK (slot_index BETWEEN 0 AND 2),
                    catalog_meal_id varchar(36),
                    is_logged boolean NOT NULL DEFAULT false,
                    logged_meal_id varchar(36),
                    version integer NOT NULL DEFAULT 1,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now(),
                    UNIQUE (plan_id, day_index, slot_index)
                )
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TABLE weekly_meal_plan_pantry_items (
                    plan_id varchar(36) NOT NULL REFERENCES weekly_meal_plans(id),
                    food_reference_id integer NOT NULL,
                    available_amount numeric(12, 4)
                )
                """
            )
            connection.commit()
            yield connection
            connection.rollback()
        with engine.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
    finally:
        engine.dispose()


def _add_plan(database, plan_id, indices, *, algorithm="v1", status="draft"):
    original_updated_at = datetime(2026, 10, 6, 7, 31, 17, tzinfo=UTC)
    database.execute(
        text(
            """
            INSERT INTO weekly_meal_plans
                (id, algorithm_version, status, revision, updated_at)
            VALUES (:id, :algorithm, :status, 4, :updated_at)
            """
        ),
        {
            "id": plan_id,
            "algorithm": algorithm,
            "status": status,
            "updated_at": original_updated_at,
        },
    )
    for day in range(7):
        for slot_index in indices:
            is_logged = day == 1 and slot_index == 1
            database.execute(
                text(
                    """
                    INSERT INTO weekly_meal_plan_slots
                        (id, plan_id, day_index, slot_index,
                         catalog_meal_id, is_logged, logged_meal_id, version)
                    VALUES (:id, :plan_id, :day, :slot_index,
                            :recipe_id, :is_logged, :logged_meal_id, :version)
                    """
                ),
                {
                    "id": f"{plan_id}-{day}-{slot_index}",
                    "plan_id": plan_id,
                    "day": day,
                    "slot_index": slot_index,
                    "recipe_id": f"recipe-{slot_index}",
                    "is_logged": is_logged,
                    "logged_meal_id": "logged-meal" if is_logged else None,
                    "version": 3 if is_logged else 1,
                },
            )
    return original_updated_at


def _run(database, action):
    with Operations.context(MigrationContext.configure(database)):
        getattr(_migration(), action)()


def _slots(database, plan_id):
    return (
        database.execute(
            text(
                """
            SELECT id, day_index, slot_index, catalog_meal_id,
                   is_logged, logged_meal_id, version
            FROM weekly_meal_plan_slots
            WHERE plan_id = :plan_id
            ORDER BY day_index, slot_index
            """
            ),
            {"plan_id": plan_id},
        )
        .mappings()
        .all()
    )


def test_late_legacy_repair_preserves_logged_slots_and_is_reversible(database):
    original_updated_at = _add_plan(database, "late", (0, 1))
    _add_plan(database, "complete", (0, 1, 2), algorithm="v2")
    _add_plan(database, "modern-partial", (0, 1), algorithm="v2")
    _add_plan(database, "ambiguous", (1, 2))
    _add_plan(database, "confirmed", (0, 1), status="confirmed")
    database.execute(
        text(
            """
            INSERT INTO weekly_meal_plan_pantry_items
                (plan_id, food_reference_id, available_amount)
            VALUES ('late', 42, 3.5)
            """
        )
    )
    before = {row["id"]: dict(row) for row in _slots(database, "late")}

    _run(database, "upgrade")
    marker_table = WeeklyPlanLateLegacySlotRepairORM.__table__
    schema = database.scalar(text("SELECT current_schema()"))
    inspector = inspect(database)
    actual_columns = {
        column["name"]: column
        for column in inspector.get_columns(marker_table.name, schema)
    }
    assert set(actual_columns) == set(marker_table.columns.keys())
    assert all(
        actual_columns[column.name]["nullable"] == column.nullable
        for column in marker_table.columns
    )
    assert set(
        inspector.get_pk_constraint(marker_table.name, schema)["constrained_columns"]
    ) == {
        "plan_id",
        "day_index",
    }
    assert any(
        unique["column_names"] == ["breakfast_slot_id"]
        for unique in inspector.get_unique_constraints(marker_table.name, schema)
    )
    assert any(
        fk["constrained_columns"] == ["plan_id"]
        and fk["referred_table"] == "weekly_meal_plans"
        and fk["options"].get("ondelete") == "CASCADE"
        for fk in inspector.get_foreign_keys(marker_table.name, schema)
    )
    repaired = _slots(database, "late")
    assert len(repaired) == 21
    assert {(row["day_index"], row["slot_index"]) for row in repaired} == {
        (day, slot) for day in range(7) for slot in range(3)
    }
    for original_id, original in before.items():
        moved = next(row for row in repaired if row["id"] == original_id)
        assert moved["slot_index"] == original["slot_index"] + 1
        assert moved["catalog_meal_id"] == original["catalog_meal_id"]
        assert moved["is_logged"] == original["is_logged"]
        assert moved["logged_meal_id"] == original["logged_meal_id"]
        assert moved["version"] == original["version"]
    assert all(
        row["catalog_meal_id"] is None and not row["is_logged"]
        for row in repaired
        if row["slot_index"] == 0
    )
    # The current-plan repository constructs this domain object on GET.
    plan = WeeklyMealPlan(
        id="late",
        user_id="user",
        week_start_date=date(2026, 10, 5),
        status=WeeklyMealPlanStatus.DRAFT,
        people=1,
        preferences=WeeklyMealPlanPreferences(),
        timezone="UTC",
        slots=tuple(
            WeeklyMealPlanSlot(
                id=row["id"],
                day_index=row["day_index"],
                slot_index=row["slot_index"],
                recipe_id=row["catalog_meal_id"],
                is_logged=row["is_logged"],
                logged_meal_id=row["logged_meal_id"],
                version=row["version"],
            )
            for row in repaired
        ),
    )
    assert len(plan.slots) == 21
    assert (
        database.scalar(
            text("SELECT revision FROM weekly_meal_plans WHERE id = 'late'")
        )
        == 5
    )
    assert (
        database.scalar(
            text(
                """
            SELECT available_amount FROM weekly_meal_plan_pantry_items
            WHERE plan_id = 'late' AND food_reference_id = 42
            """
            )
        )
        == 3.5
    )

    _run(database, "upgrade")
    assert _slots(database, "late") == repaired
    assert (
        database.scalar(
            text("SELECT revision FROM weekly_meal_plans WHERE id = 'late'")
        )
        == 5
    )
    for untouched in ("complete", "modern-partial", "ambiguous", "confirmed"):
        assert len(_slots(database, untouched)) == (
            21 if untouched == "complete" else 14
        )
        assert (
            database.scalar(
                text("SELECT revision FROM weekly_meal_plans WHERE id = :id"),
                {"id": untouched},
            )
            == 4
        )

    _run(database, "downgrade")
    assert {row["id"]: dict(row) for row in _slots(database, "late")} == before
    assert database.execute(
        text("SELECT revision, updated_at FROM weekly_meal_plans WHERE id = 'late'")
    ).one() == (4, original_updated_at)
    assert (
        database.scalar(
            text(
                "SELECT to_regclass(format('%I.%I', current_schema(), "
                "'weekly_plan_late_legacy_slot_repairs'))"
            )
        )
        is None
    )


def test_downgrade_refuses_to_remove_edited_breakfast(database):
    _add_plan(database, "late", (0, 1))
    _run(database, "upgrade")
    database.execute(
        text(
            """
            UPDATE weekly_meal_plan_slots
            SET catalog_meal_id = 'new-breakfast'
            WHERE plan_id = 'late' AND day_index = 0 AND slot_index = 0
            """
        )
    )
    with pytest.raises(DBAPIError, match="Cannot reverse a changed weekly plan"):
        with database.begin_nested():
            _run(database, "downgrade")
    assert len(_slots(database, "late")) == 21
