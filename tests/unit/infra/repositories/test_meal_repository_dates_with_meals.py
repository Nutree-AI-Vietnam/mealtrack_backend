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
