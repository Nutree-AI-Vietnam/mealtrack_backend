"""UpdateNotificationPreferencesCommandHandler with per-meal switches and engagement toggles."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.app.commands.notification import UpdateNotificationPreferencesCommand
from src.app.handlers.command_handlers.update_notification_preferences_command_handler import (
    UpdateNotificationPreferencesCommandHandler,
)
from src.domain.model.notification import NotificationPreferences

USER_ID = "00000000-0000-0000-0000-0000000000a1"
_UOW_PATH = (
    "src.app.handlers.command_handlers."
    "update_notification_preferences_command_handler.AsyncUnitOfWork"
)
TOGGLES = (
    "streak_reminders_enabled",
    "reengagement_enabled",
    "progress_updates_enabled",
    "offers_enabled",
)


async def _run(fake_uow: FakeUnitOfWork, command: UpdateNotificationPreferencesCommand):
    publisher = AsyncMock()
    handler = UpdateNotificationPreferencesCommandHandler(event_publisher=publisher)
    with patch(_UOW_PATH, return_value=fake_uow):
        result = await handler.handle(command)
    return result, publisher


@pytest.mark.asyncio
async def test_per_meal_update_persists_switches_and_water_time():
    fake_uow = FakeUnitOfWork()

    result, publisher = await _run(
        fake_uow,
        UpdateNotificationPreferencesCommand(
            user_id=USER_ID,
            dinner_reminder_enabled=False,
            hydration_reminder_time_minutes=1020,
        ),
    )

    stored = fake_uow.notifications.preferences[USER_ID]
    assert stored.dinner_reminder_enabled is False
    assert stored.breakfast_reminder_enabled is True
    assert stored.hydration_reminder_time_minutes == 1020
    assert result["success"] is True
    preferences = result["preferences"]
    assert preferences["dinner_reminder_enabled"] is False
    assert preferences["meal_reminders_enabled"] is True
    assert preferences["hydration_reminder_time_minutes"] == 1020
    assert fake_uow.committed is True
    publisher.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_older_app_saving_another_field_keeps_a_meal_off():
    fake_uow = FakeUnitOfWork()
    await fake_uow.notifications.save_notification_preferences(
        NotificationPreferences.create_default(USER_ID).update_preferences(
            dinner_reminder_enabled=False
        )
    )

    result, _ = await _run(
        fake_uow,
        UpdateNotificationPreferencesCommand(
            user_id=USER_ID,
            meal_reminders_enabled=True,
            daily_summary_time_minutes=1200,
        ),
    )

    preferences = result["preferences"]
    assert preferences["dinner_reminder_enabled"] is False
    assert preferences["breakfast_reminder_enabled"] is True
    assert preferences["daily_summary_time_minutes"] == 1200


@pytest.mark.asyncio
async def test_older_app_turning_meal_reminders_off_turns_off_every_meal():
    fake_uow = FakeUnitOfWork()

    result, _ = await _run(
        fake_uow,
        UpdateNotificationPreferencesCommand(
            user_id=USER_ID, meal_reminders_enabled=False
        ),
    )

    preferences = result["preferences"]
    assert preferences["meal_reminders_enabled"] is False
    assert preferences["breakfast_reminder_enabled"] is False
    assert preferences["lunch_reminder_enabled"] is False
    assert preferences["dinner_reminder_enabled"] is False


async def _handle(fake_uow: FakeUnitOfWork, command, publisher=None):
    handler = UpdateNotificationPreferencesCommandHandler(
        event_publisher=publisher or AsyncMock()
    )
    with patch(_UOW_PATH, return_value=fake_uow):
        return await handler.handle(command)


def _toggles(source) -> dict[str, bool]:
    if isinstance(source, dict):
        return {name: source[name] for name in TOGGLES}
    return {name: getattr(source, name) for name in TOGGLES}


@pytest.mark.asyncio
async def test_sent_toggles_are_stored_and_returned():
    user_id = str(uuid4())
    fake_uow = FakeUnitOfWork()
    fake_uow.notifications.preferences[user_id] = (
        NotificationPreferences.create_default(user_id)
    )
    publisher = AsyncMock()

    result = await _handle(
        fake_uow,
        UpdateNotificationPreferencesCommand(
            user_id=user_id,
            streak_reminders_enabled=False,
            reengagement_enabled=False,
            progress_updates_enabled=False,
            offers_enabled=True,
        ),
        publisher,
    )

    expected = {
        "streak_reminders_enabled": False,
        "reengagement_enabled": False,
        "progress_updates_enabled": False,
        "offers_enabled": True,
    }
    assert result["success"] is True
    assert _toggles(result["preferences"]) == expected
    assert _toggles(fake_uow.notifications.preferences[user_id]) == expected
    payload = publisher.publish.await_args.args[0]
    assert _toggles(payload["data"]["notification_preferences"]) == expected


@pytest.mark.asyncio
async def test_a_request_without_the_toggles_keeps_the_stored_values():
    user_id = str(uuid4())
    fake_uow = FakeUnitOfWork()
    stored = NotificationPreferences.create_default(user_id).update_preferences(
        streak_reminders_enabled=False, offers_enabled=True
    )
    fake_uow.notifications.preferences[user_id] = stored

    result = await _handle(
        fake_uow,
        UpdateNotificationPreferencesCommand(
            user_id=user_id, hydration_reminders_enabled=False
        ),
    )

    assert result["preferences"]["hydration_reminders_enabled"] is False
    assert _toggles(result["preferences"]) == {
        "streak_reminders_enabled": False,
        "reengagement_enabled": True,
        "progress_updates_enabled": True,
        "offers_enabled": True,
    }


@pytest.mark.asyncio
async def test_first_update_starts_from_the_defaults():
    user_id = str(uuid4())
    fake_uow = FakeUnitOfWork()

    result = await _handle(
        fake_uow,
        UpdateNotificationPreferencesCommand(
            user_id=user_id, progress_updates_enabled=False
        ),
    )

    assert _toggles(result["preferences"]) == {
        "streak_reminders_enabled": True,
        "reengagement_enabled": True,
        "progress_updates_enabled": False,
        "offers_enabled": False,
    }
