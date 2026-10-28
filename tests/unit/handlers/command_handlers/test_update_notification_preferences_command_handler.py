"""UpdateNotificationPreferencesCommandHandler with per-meal switches."""

from unittest.mock import AsyncMock, patch

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
