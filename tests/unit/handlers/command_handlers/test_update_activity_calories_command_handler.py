from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.app.commands.user.update_activity_calories_command import (
    UpdateActivityCaloriesCommand,
)
from src.app.handlers.command_handlers.update_activity_calories_command_handler import (
    UpdateActivityCaloriesCommandHandler,
)

_MODULE = "src.app.handlers.command_handlers.update_activity_calories_command_handler"
_ZONE = "Asia/Ho_Chi_Minh"


def _local_today():
    return datetime.now(ZoneInfo(_ZONE)).date()


@pytest.mark.asyncio
async def test_turning_off_persists_drops_today_snapshot_purges_and_publishes():
    user_id = str(uuid4())
    fake_uow = FakeUnitOfWork()
    fake_uow.daily_target_snapshots = AsyncMock()
    publisher = AsyncMock()
    purge = AsyncMock()
    handler = UpdateActivityCaloriesCommandHandler(
        lambda: fake_uow, event_publisher=publisher, purge_caches=purge
    )

    before = _local_today()
    with patch(
        f"{_MODULE}.resolve_user_timezone_async",
        AsyncMock(return_value=_ZONE),
    ):
        result = await handler.handle(
            UpdateActivityCaloriesCommand(user_id=user_id, enabled=False)
        )
    after = _local_today()

    assert result == {"enabled": False}
    assert fake_uow.users.activity_calories[user_id] is False
    assert fake_uow.committed is True
    # Today's target was stored under the old setting; the user's local day.
    fake_uow.daily_target_snapshots.delete_for_date.assert_awaited_once()
    snapshot_user, snapshot_day = (
        fake_uow.daily_target_snapshots.delete_for_date.await_args.args
    )
    assert snapshot_user == user_id
    assert snapshot_day in {before, after}
    purge.assert_awaited_once_with(user_id, snapshot_day)
    publisher.publish.assert_awaited_once()
    payload = publisher.publish.await_args.args[0]
    assert payload["data"] == {"user_id": user_id, "activity_calories": False}


@pytest.mark.asyncio
async def test_unchanged_setting_skips_write_purge_and_publish():
    user_id = str(uuid4())
    fake_uow = FakeUnitOfWork()
    fake_uow.daily_target_snapshots = AsyncMock()
    publisher = AsyncMock()
    purge = AsyncMock()
    handler = UpdateActivityCaloriesCommandHandler(
        lambda: fake_uow, event_publisher=publisher, purge_caches=purge
    )

    result = await handler.handle(
        UpdateActivityCaloriesCommand(user_id=user_id, enabled=True)
    )

    assert result == {"enabled": True}
    assert user_id not in fake_uow.users.activity_calories
    fake_uow.daily_target_snapshots.delete_for_date.assert_not_awaited()
    purge.assert_not_awaited()
    publisher.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_turning_back_on_is_stored():
    user_id = str(uuid4())
    fake_uow = FakeUnitOfWork()
    fake_uow.users.activity_calories[user_id] = False
    handler = UpdateActivityCaloriesCommandHandler(
        lambda: fake_uow, event_publisher=AsyncMock()
    )

    result = await handler.handle(
        UpdateActivityCaloriesCommand(user_id=user_id, enabled=True)
    )

    assert result == {"enabled": True}
    assert fake_uow.users.activity_calories[user_id] is True
