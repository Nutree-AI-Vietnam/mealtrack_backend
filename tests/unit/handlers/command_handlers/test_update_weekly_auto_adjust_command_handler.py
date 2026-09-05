from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.app.commands.user.update_weekly_auto_adjust_command import (
    UpdateWeeklyAutoAdjustCommand,
)
from src.app.handlers.command_handlers.update_weekly_auto_adjust_command_handler import (
    UpdateWeeklyAutoAdjustCommandHandler,
)


@pytest.mark.asyncio
async def test_update_weekly_auto_adjust_persists_and_publishes():
    user_id = uuid4()
    fake_uow = FakeUnitOfWork()
    publisher = AsyncMock()
    handler = UpdateWeeklyAutoAdjustCommandHandler(event_publisher=publisher)

    with patch(
        "src.app.handlers.command_handlers.update_weekly_auto_adjust_command_handler.AsyncUnitOfWork",
        return_value=fake_uow,
    ):
        result = await handler.handle(
            UpdateWeeklyAutoAdjustCommand(user_id=user_id, enabled=False)
        )

    assert result == {"success": True, "enabled": False}
    assert fake_uow.users.weekly_auto_adjust[str(user_id)] is False
    publisher.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_weekly_auto_adjust_skips_write_when_unchanged():
    user_id = uuid4()
    fake_uow = FakeUnitOfWork()
    publisher = AsyncMock()
    handler = UpdateWeeklyAutoAdjustCommandHandler(event_publisher=publisher)

    with patch(
        "src.app.handlers.command_handlers.update_weekly_auto_adjust_command_handler.AsyncUnitOfWork",
        return_value=fake_uow,
    ):
        result = await handler.handle(
            UpdateWeeklyAutoAdjustCommand(user_id=user_id, enabled=True)
        )

    assert result == {"success": True, "enabled": True}
    publisher.publish.assert_not_awaited()
