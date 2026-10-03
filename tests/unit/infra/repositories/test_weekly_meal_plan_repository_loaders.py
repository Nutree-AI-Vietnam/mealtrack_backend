"""Owner and lock ordering guards for weekly planner reads."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from src.infra.repositories.weekly_meal_plan_repository_async import (
    AsyncWeeklyMealPlanRepository,
)


def _session(row=None):
    session = MagicMock()
    session.get_bind.return_value.dialect.name = "postgresql"
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session.execute = AsyncMock(return_value=result)
    return session


def _sql(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_missing_owner_plan_does_not_lock_unrelated_slots():
    session = _session()
    result = await AsyncWeeklyMealPlanRepository(session).get_for_update(
        user_id="other-user", plan_id="plan", include_pantry=False
    )
    assert result is None
    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    sql = _sql(statement)
    assert "weekly_meal_plans.user_id =" in sql
    assert sql.endswith("FOR UPDATE")


@pytest.mark.asyncio
async def test_plan_mutation_locks_parent_before_coordinate_ordered_slots():
    session = _session(SimpleNamespace(id="plan"))
    await AsyncWeeklyMealPlanRepository(session).get_for_update(
        user_id="user", plan_id="plan", include_pantry=False
    )
    parent, slots = [call.args[0] for call in session.execute.await_args_list]
    assert _sql(parent).endswith("FOR UPDATE")
    assert (
        "ORDER BY weekly_meal_plan_slots.day_index, weekly_meal_plan_slots.slot_index"
        in _sql(slots)
    )
    assert _sql(slots).endswith("FOR UPDATE")


@pytest.mark.asyncio
async def test_missing_week_advisory_lock_is_stable_and_owner_scoped():
    keys = []
    for user_id in ("user", "user", "other-user"):
        session = _session()
        await AsyncWeeklyMealPlanRepository(session).lock_user_week(
            user_id=user_id, week_start_date=date(2026, 9, 21)
        )
        advisory, row = session.execute.await_args_list
        assert "pg_advisory_xact_lock" in str(advisory.args[0])
        keys.append(advisory.args[1]["key"])
        assert _sql(row.args[0]).endswith("FOR UPDATE")
    assert keys[0] == keys[1]
    assert keys[0] != keys[2]
    assert -(2**63) <= keys[0] < 2**63
