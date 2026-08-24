from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.commands.notification import RegisterFcmTokenCommand
from src.app.handlers.command_handlers.register_fcm_token_command_handler import (
    RegisterFcmTokenCommandHandler,
)
from src.infra.event_bus.background_task_manager import BackgroundTaskManager


def _mock_uow(current_timezone="UTC"):
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.users.get_user_timezone = AsyncMock(return_value=current_timezone)
    uow.users.update_user_timezone = AsyncMock()
    return uow


def _mock_notification_repo():
    repo = MagicMock()
    repo.find_active_fcm_tokens_by_user = AsyncMock(return_value=[])
    repo.save_fcm_token = AsyncMock(return_value=MagicMock(token_id="token-id"))
    repo.deactivate_fcm_token = AsyncMock()
    return repo


@pytest.mark.asyncio
async def test_register_fcm_token_reschedules_when_timezone_changes():
    user_id = str(uuid4())
    publisher = AsyncMock()
    repo = _mock_notification_repo()
    uow = _mock_uow(current_timezone="UTC")
    handler = RegisterFcmTokenCommandHandler(
        notification_repository=repo,
        event_publisher=publisher,
    )

    with patch(
        "src.app.handlers.command_handlers.register_fcm_token_command_handler.AsyncUnitOfWork",
        return_value=uow,
    ):
        result = await handler.handle(
            RegisterFcmTokenCommand(
                user_id=user_id,
                fcm_token="fcm-token-valid-123",
                device_type="ios",
                timezone="Asia/Ho_Chi_Minh",
            )
        )

    assert result["success"] is True
    uow.users.update_user_timezone.assert_awaited_once_with(user_id, "Asia/Ho_Chi_Minh")
    publisher.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_fcm_token_skips_reschedule_when_timezone_unchanged():
    user_id = str(uuid4())
    publisher = AsyncMock()
    repo = _mock_notification_repo()
    uow = _mock_uow(current_timezone="Asia/Ho_Chi_Minh")
    handler = RegisterFcmTokenCommandHandler(
        notification_repository=repo,
        event_publisher=publisher,
    )

    with patch(
        "src.app.handlers.command_handlers.register_fcm_token_command_handler.AsyncUnitOfWork",
        return_value=uow,
    ):
        result = await handler.handle(
            RegisterFcmTokenCommand(
                user_id=user_id,
                fcm_token="fcm-token-valid-123",
                device_type="ios",
                timezone="Asia/Ho_Chi_Minh",
            )
        )

    assert result["success"] is True
    uow.users.update_user_timezone.assert_not_called()
    publisher.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_register_fcm_token_ignores_invalid_timezone_without_reschedule():
    user_id = str(uuid4())
    publisher = AsyncMock()
    repo = _mock_notification_repo()
    uow = _mock_uow(current_timezone="UTC")
    handler = RegisterFcmTokenCommandHandler(
        notification_repository=repo,
        event_publisher=publisher,
    )

    with patch(
        "src.app.handlers.command_handlers.register_fcm_token_command_handler.AsyncUnitOfWork",
        return_value=uow,
    ):
        result = await handler.handle(
            RegisterFcmTokenCommand(
                user_id=user_id,
                fcm_token="fcm-token-valid-123",
                device_type="ios",
                timezone="Not/AZone",
            )
        )

    assert result["success"] is True
    uow.users.update_user_timezone.assert_not_called()
    publisher.publish.assert_not_awaited()
