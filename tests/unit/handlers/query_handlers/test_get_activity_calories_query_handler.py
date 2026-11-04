import pytest
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.app.handlers.query_handlers.get_activity_calories_query_handler import (
    GetActivityCaloriesQueryHandler,
)
from src.app.queries.user.get_activity_calories_query import GetActivityCaloriesQuery


@pytest.mark.asyncio
async def test_get_activity_calories_defaults_enabled():
    fake_uow = FakeUnitOfWork()
    handler = GetActivityCaloriesQueryHandler(lambda: fake_uow)

    result = await handler.handle(GetActivityCaloriesQuery(user_id="user-1"))

    assert result == {"enabled": True}


@pytest.mark.asyncio
async def test_get_activity_calories_returns_disabled():
    fake_uow = FakeUnitOfWork()
    fake_uow.users.activity_calories["user-1"] = False
    handler = GetActivityCaloriesQueryHandler(lambda: fake_uow)

    result = await handler.handle(GetActivityCaloriesQuery(user_id="user-1"))

    assert result == {"enabled": False}
