import pytest
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.app.handlers.query_handlers.get_weekly_auto_adjust_query_handler import (
    GetWeeklyAutoAdjustQueryHandler,
)
from src.app.queries.user.get_weekly_auto_adjust_query import GetWeeklyAutoAdjustQuery


@pytest.mark.asyncio
async def test_get_weekly_auto_adjust_defaults_enabled():
    fake_uow = FakeUnitOfWork()
    handler = GetWeeklyAutoAdjustQueryHandler(lambda: fake_uow)

    result = await handler.handle(GetWeeklyAutoAdjustQuery(user_id="user-1"))

    assert result == {"enabled": True}


@pytest.mark.asyncio
async def test_get_weekly_auto_adjust_returns_disabled():
    fake_uow = FakeUnitOfWork()
    fake_uow.users.weekly_auto_adjust["user-1"] = False
    handler = GetWeeklyAutoAdjustQueryHandler(lambda: fake_uow)

    result = await handler.handle(GetWeeklyAutoAdjustQuery(user_id="user-1"))

    assert result == {"enabled": False}
