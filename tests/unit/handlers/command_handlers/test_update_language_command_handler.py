from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.app.commands.user.update_language_command import UpdateLanguageCommand
from src.app.handlers.command_handlers.update_language_command_handler import (
    UpdateLanguageCommandHandler,
)
from src.domain.model.notification import NotificationPreferences
from src.infra.event_bus.background_task_manager import BackgroundTaskManager


@pytest.mark.asyncio
async def test_update_language_syncs_notification_preferences_and_reschedules():
    user_id = str(uuid4())
    fake_uow = FakeUnitOfWork()
    fake_uow.notifications.preferences[user_id] = (
        NotificationPreferences.create_default(user_id)
    )
    publisher = AsyncMock()
    handler = UpdateLanguageCommandHandler(event_publisher=publisher)

    with patch(
        "src.app.handlers.command_handlers.update_language_command_handler.AsyncUnitOfWork",
        return_value=fake_uow,
    ):
        result = await handler.handle(
            UpdateLanguageCommand(user_id=user_id, language_code="vi")
        )

    assert result == {"success": True, "language_code": "vi"}
    assert fake_uow.notifications.preferences[user_id].language == "vi"
    publisher.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_language_rejects_unsupported_language_without_reschedule():
    publisher = AsyncMock()
    handler = UpdateLanguageCommandHandler(event_publisher=publisher)

    result = await handler.handle(
        UpdateLanguageCommand(user_id=uuid4(), language_code="unsupported")
    )

    assert result["success"] is False
    publisher.publish.assert_not_awaited()
