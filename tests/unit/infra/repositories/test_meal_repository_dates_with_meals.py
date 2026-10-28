"""get_dates_with_meals: the days a user logged food, on the user's clock."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from src.infra.repositories.meal_repository_async import AsyncMealRepository


class _Result:
    def all(self):
        return []


async def _sql(user_timezone: str) -> str:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_Result())

    await AsyncMealRepository(session).get_dates_with_meals("user-1", user_timezone)

    statement = session.execute.await_args.args[0]
    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_local_days_read_created_at_as_utc():
    # created_at holds UTC without a zone. Handed to timezone() bare, it is
    # read as local time, and a 9 a.m. meal in Hanoi lands on the day before.
    sql = await _sql("Asia/Ho_Chi_Minh")

    expected = (
        "date(timezone(%(timezone_1)s, "
        "CAST(meal.created_at AS TIMESTAMP WITH TIME ZONE)))"
    )
    assert expected in sql


@pytest.mark.asyncio
async def test_utc_days_need_no_conversion():
    sql = await _sql("UTC")

    assert "date(meal.created_at)" in sql
    assert "timezone(" not in sql


async def _streak_sql(user_timezone: str) -> str:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_Result())

    await AsyncMealRepository(session).get_streak_log_days("user-1", user_timezone)

    statement = session.execute.await_args.args[0]
    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_streak_days_read_the_stored_flag_then_the_noon_stamp_on_the_users_clock():
    sql = await _streak_sql("Asia/Ho_Chi_Minh")

    local = (
        "timezone(%(timezone_1)s, CAST(meal.created_at AS TIMESTAMP WITH TIME ZONE))"
    )
    assert f"date({local})" in sql
    # Rows from before the flag was stored keep the noon-stamp rule.
    assert (
        "bool_or(coalesce(meal.logged_on_day, "
        f"CAST({local} AS TIME WITHOUT TIME ZONE) != %(noon)s))"
    ) in sql
    assert f"GROUP BY date({local})" in sql


@pytest.mark.asyncio
async def test_utc_streak_days_need_no_conversion():
    sql = await _streak_sql("UTC")

    assert (
        "bool_or(coalesce(meal.logged_on_day, "
        "CAST(meal.created_at AS TIME WITHOUT TIME ZONE) != %(noon)s))"
    ) in sql
    assert "timezone(" not in sql
