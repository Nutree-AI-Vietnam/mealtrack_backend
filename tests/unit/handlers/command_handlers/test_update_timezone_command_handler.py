"""Unit tests: timezone update handler skips DB write when tz is unchanged."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.commands.user.update_timezone_command import UpdateTimezoneCommand
from src.app.handlers.command_handlers.update_timezone_command_handler import (
    UpdateTimezoneCommandHandler,
)


@pytest.mark.asyncio
async def test_skips_db_write_when_timezone_unchanged():
    """If the stored timezone equals the incoming one, no DB write occurs."""
    publisher = AsyncMock()
    handler = UpdateTimezoneCommandHandler(event_publisher=publisher)
    command = UpdateTimezoneCommand(user_id="user-1", timezone="Asia/Ho_Chi_Minh")

    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users.get_user_timezone = AsyncMock(
        return_value="Asia/Ho_Chi_Minh"
    )  # same
    mock_uow.users.update_user_timezone = AsyncMock()

    with patch(
        "src.app.handlers.command_handlers.update_timezone_command_handler.AsyncUnitOfWork",
        return_value=mock_uow,
    ):
        result = await handler.handle(command)

    assert result == {"success": True, "timezone": "Asia/Ho_Chi_Minh"}
    mock_uow.users.update_user_timezone.assert_not_called()
    publisher.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_writes_db_when_timezone_changed():
    """If the stored timezone differs, the update proceeds."""
    publisher = AsyncMock()
    handler = UpdateTimezoneCommandHandler(event_publisher=publisher)
    command = UpdateTimezoneCommand(user_id="user-1", timezone="America/New_York")

    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users.get_user_timezone = AsyncMock(return_value="UTC")  # different
    mock_uow.users.update_user_timezone = AsyncMock()
    mock_uow.commit = AsyncMock()

    with patch(
        "src.app.handlers.command_handlers.update_timezone_command_handler.AsyncUnitOfWork",
        return_value=mock_uow,
    ):
        result = await handler.handle(command)

    assert result["success"] is True
    mock_uow.users.update_user_timezone.assert_called_once_with(
        "user-1", "America/New_York"
    )
    publisher.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_writes_db_when_no_stored_timezone():
    """If no timezone is stored yet (None), the update proceeds."""
    publisher = AsyncMock()
    handler = UpdateTimezoneCommandHandler(event_publisher=publisher)
    command = UpdateTimezoneCommand(user_id="user-1", timezone="Asia/Ho_Chi_Minh")

    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users.get_user_timezone = AsyncMock(return_value=None)
    mock_uow.users.update_user_timezone = AsyncMock()
    mock_uow.commit = AsyncMock()

    with patch(
        "src.app.handlers.command_handlers.update_timezone_command_handler.AsyncUnitOfWork",
        return_value=mock_uow,
    ):
        result = await handler.handle(command)

    assert result["success"] is True
    mock_uow.users.update_user_timezone.assert_called_once_with(
        "user-1", "Asia/Ho_Chi_Minh"
    )
    publisher.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_timezone_write_refreshes_the_auth_snapshot():
    """A committed timezone update must not leave the auth snapshot stale."""
    from src.api.dependencies.auth_cache import (
        cached_user_timezone,
        set_cached_user_id,
    )
    from src.domain.utils.request_user_timezone import bind_request_user_timezone
    from src.domain.utils.timezone_utils import resolve_user_timezone_async

    await set_cached_user_id(None, "fb-user-1", "user-1", True, timezone="UTC")
    bind_request_user_timezone("user-1", "UTC")

    publisher = AsyncMock()
    handler = UpdateTimezoneCommandHandler(event_publisher=publisher)
    command = UpdateTimezoneCommand(user_id="user-1", timezone="America/New_York")

    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    mock_uow.users.update_user_timezone = AsyncMock()
    mock_uow.commit = AsyncMock()

    with patch(
        "src.app.handlers.command_handlers.update_timezone_command_handler.AsyncUnitOfWork",
        return_value=mock_uow,
    ):
        result = await handler.handle(command)

    assert result["timezone"] == "America/New_York"
    assert cached_user_timezone("fb-user-1") == "America/New_York"

    reader = MagicMock()
    reader.users.get_user_timezone = AsyncMock(return_value="UTC")
    resolved = await resolve_user_timezone_async("user-1", reader, "Asia/Tokyo")
    assert resolved == "America/New_York"
    reader.users.get_user_timezone.assert_not_called()


@pytest.mark.asyncio
async def test_unchanged_timezone_still_refreshes_a_stale_auth_snapshot():
    """Skipping the UPDATE still drops a snapshot that disagrees with the row."""
    from src.api.dependencies.auth_cache import (
        cached_user_timezone,
        set_cached_user_id,
    )

    await set_cached_user_id(
        None, "fb-user-1", "user-1", True, timezone="America/Los_Angeles"
    )

    publisher = AsyncMock()
    handler = UpdateTimezoneCommandHandler(event_publisher=publisher)
    command = UpdateTimezoneCommand(user_id="user-1", timezone="Asia/Ho_Chi_Minh")

    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users.get_user_timezone = AsyncMock(return_value="Asia/Ho_Chi_Minh")
    mock_uow.users.update_user_timezone = AsyncMock()

    with patch(
        "src.app.handlers.command_handlers.update_timezone_command_handler.AsyncUnitOfWork",
        return_value=mock_uow,
    ):
        await handler.handle(command)

    mock_uow.users.update_user_timezone.assert_not_called()
    assert cached_user_timezone("fb-user-1") == "Asia/Ho_Chi_Minh"


@pytest.mark.asyncio
async def test_rejects_invalid_timezone_without_reschedule():
    """Invalid timezone input should not write or reschedule."""
    from src.api.dependencies.auth_cache import (
        cached_user_timezone,
        set_cached_user_id,
    )

    await set_cached_user_id(None, "fb-user-1", "user-1", True, timezone="UTC")
    publisher = AsyncMock()
    handler = UpdateTimezoneCommandHandler(event_publisher=publisher)

    result = await handler.handle(
        UpdateTimezoneCommand(user_id="user-1", timezone="Not/AZone")
    )

    assert result["success"] is False
    publisher.publish.assert_not_awaited()
    assert cached_user_timezone("fb-user-1") == "UTC"
