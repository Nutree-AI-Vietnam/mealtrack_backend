from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.domain.model.notification import NotificationPreferences
from src.infra.repositories.notification_repository_async import (
    AsyncNotificationRepository,
)


class _FakeScalarResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows=None):
        self._scalar_result = _FakeScalarResult(rows=rows)

    def scalars(self):
        return self._scalar_result


class _FakeSession:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []

    async def execute(self, statement):
        del statement
        return _FakeResult(rows=[self.existing] if self.existing else [])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def _prefs(*, hydration_reminders_enabled: bool) -> NotificationPreferences:
    now = datetime(2026, 9, 16, 15, 17, tzinfo=timezone.utc)
    return NotificationPreferences(
        preferences_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        meal_reminders_enabled=True,
        daily_summary_enabled=True,
        hydration_reminders_enabled=hydration_reminders_enabled,
        breakfast_time_minutes=510,
        lunch_time_minutes=690,
        dinner_time_minutes=1080,
        daily_summary_time_minutes=1260,
        language="vi",
        created_at=now,
        updated_at=now,
    )


def _existing_row(*, hydration_reminders_enabled: bool = True):
    now = datetime(2026, 9, 16, 11, 7, tzinfo=timezone.utc)
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        meal_reminders_enabled=True,
        breakfast_reminder_enabled=True,
        lunch_reminder_enabled=True,
        dinner_reminder_enabled=True,
        daily_summary_enabled=True,
        hydration_reminders_enabled=hydration_reminders_enabled,
        breakfast_time_minutes=510,
        lunch_time_minutes=690,
        dinner_time_minutes=1080,
        daily_summary_time_minutes=1260,
        hydration_reminder_time_minutes=930,
        language="vi",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_save_updates_hydration_reminders_on_existing_row():
    existing = _existing_row(hydration_reminders_enabled=True)
    repository = AsyncNotificationRepository(_FakeSession(existing=existing))

    saved = await repository.save_notification_preferences(
        _prefs(hydration_reminders_enabled=False)
    )

    assert existing.hydration_reminders_enabled is False
    assert saved.hydration_reminders_enabled is False


@pytest.mark.asyncio
async def test_save_inserts_hydration_reminders_on_new_row():
    session = _FakeSession(existing=None)
    repository = AsyncNotificationRepository(session)

    saved = await repository.save_notification_preferences(
        _prefs(hydration_reminders_enabled=False)
    )

    assert len(session.added) == 1
    assert session.added[0].hydration_reminders_enabled is False
    assert saved.hydration_reminders_enabled is False


def _per_meal_prefs() -> NotificationPreferences:
    return _prefs(hydration_reminders_enabled=True).update_preferences(
        dinner_reminder_enabled=False, hydration_reminder_time_minutes=780
    )


def _assert_per_meal_values(target) -> None:
    assert target.breakfast_reminder_enabled is True
    assert target.lunch_reminder_enabled is True
    assert target.dinner_reminder_enabled is False
    assert target.meal_reminders_enabled is True
    assert target.hydration_reminder_time_minutes == 780


@pytest.mark.asyncio
async def test_save_round_trips_meal_switches_and_water_time_on_existing_row():
    existing = _existing_row()
    repository = AsyncNotificationRepository(_FakeSession(existing=existing))

    saved = await repository.save_notification_preferences(_per_meal_prefs())

    _assert_per_meal_values(existing)
    _assert_per_meal_values(saved)


@pytest.mark.asyncio
async def test_save_round_trips_meal_switches_and_water_time_on_new_row():
    session = _FakeSession(existing=None)
    repository = AsyncNotificationRepository(session)

    saved = await repository.save_notification_preferences(_per_meal_prefs())

    assert len(session.added) == 1
    _assert_per_meal_values(session.added[0])
    _assert_per_meal_values(saved)
