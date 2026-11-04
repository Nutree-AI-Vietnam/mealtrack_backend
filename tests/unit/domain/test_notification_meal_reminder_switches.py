"""Per-meal reminder switches, the legacy all-meals flag, and the water time."""

import pytest

from src.domain.model.notification import NotificationPreferences, NotificationType
from src.domain.model.notification.notification_preferences import (
    DEFAULT_HYDRATION_REMINDER_MINUTES,
)

USER_ID = "00000000-0000-0000-0000-000000000002"


def _prefs(breakfast: bool, lunch: bool, dinner: bool) -> NotificationPreferences:
    return NotificationPreferences.create_default(USER_ID).update_preferences(
        breakfast_reminder_enabled=breakfast,
        lunch_reminder_enabled=lunch,
        dinner_reminder_enabled=dinner,
    )


def _switches(prefs: NotificationPreferences) -> tuple[bool, bool, bool]:
    return (
        prefs.breakfast_reminder_enabled,
        prefs.lunch_reminder_enabled,
        prefs.dinner_reminder_enabled,
    )


@pytest.mark.unit
class TestLegacyMealFlag:
    def test_legacy_false_turns_every_meal_off(self):
        updated = _prefs(True, True, False).update_preferences(
            meal_reminders_enabled=False
        )

        assert _switches(updated) == (False, False, False)
        assert updated.meal_reminders_enabled is False

    def test_legacy_true_turns_every_meal_on_when_all_were_off(self):
        updated = _prefs(False, False, False).update_preferences(
            meal_reminders_enabled=True
        )

        assert _switches(updated) == (True, True, True)
        assert updated.meal_reminders_enabled is True

    def test_legacy_flag_saved_with_another_field_keeps_a_meal_off(self):
        updated = _prefs(True, True, False).update_preferences(
            meal_reminders_enabled=True, daily_summary_time_minutes=1200
        )

        assert _switches(updated) == (True, True, False)
        assert updated.meal_reminders_enabled is True
        assert updated.daily_summary_time_minutes == 1200

    def test_update_without_meal_values_keeps_switches(self):
        updated = _prefs(False, True, False).update_preferences(language="vi")

        assert _switches(updated) == (False, True, False)
        assert updated.meal_reminders_enabled is True


@pytest.mark.unit
class TestPerMealSwitches:
    def test_turning_off_dinner_keeps_legacy_flag_on(self):
        updated = NotificationPreferences.create_default(USER_ID).update_preferences(
            dinner_reminder_enabled=False
        )

        assert _switches(updated) == (True, True, False)
        assert updated.meal_reminders_enabled is True

    def test_per_meal_values_win_over_the_legacy_flag(self):
        updated = _prefs(True, True, True).update_preferences(
            meal_reminders_enabled=True,
            breakfast_reminder_enabled=False,
            lunch_reminder_enabled=False,
            dinner_reminder_enabled=False,
        )

        assert _switches(updated) == (False, False, False)
        assert updated.meal_reminders_enabled is False

    def test_one_per_meal_value_ignores_a_conflicting_legacy_flag(self):
        updated = _prefs(True, True, True).update_preferences(
            meal_reminders_enabled=False, lunch_reminder_enabled=False
        )

        assert _switches(updated) == (True, False, True)
        assert updated.meal_reminders_enabled is True

    def test_each_meal_type_reads_its_own_switch(self):
        prefs = _prefs(True, False, True)

        assert prefs.is_notification_type_enabled(
            NotificationType.MEAL_REMINDER_BREAKFAST
        )
        assert not prefs.is_notification_type_enabled(
            NotificationType.MEAL_REMINDER_LUNCH
        )
        assert prefs.is_notification_type_enabled(NotificationType.MEAL_REMINDER_DINNER)


@pytest.mark.unit
class TestHydrationReminderTime:
    def test_update_sets_the_time(self):
        updated = NotificationPreferences.create_default(USER_ID).update_preferences(
            hydration_reminder_time_minutes=780
        )

        assert updated.hydration_reminder_time_minutes == 780
        assert updated.to_dict()["hydration_reminder_time_minutes"] == 780

    @pytest.mark.parametrize("minutes", [-1, 1440])
    def test_update_rejects_a_time_outside_the_day(self, minutes: int):
        prefs = NotificationPreferences.create_default(USER_ID)

        with pytest.raises(ValueError, match="hydration_reminder_time_minutes"):
            prefs.update_preferences(hydration_reminder_time_minutes=minutes)

    def test_constructor_rejects_a_time_outside_the_day(self):
        with pytest.raises(ValueError, match="hydration_reminder_time_minutes"):
            NotificationPreferences(
                preferences_id="00000000-0000-0000-0000-000000000001",
                user_id=USER_ID,
                hydration_reminder_time_minutes=1440,
            )


@pytest.mark.unit
def test_create_default_turns_every_reminder_on_with_water_at_1530():
    prefs = NotificationPreferences.create_default(USER_ID)

    assert _switches(prefs) == (True, True, True)
    assert prefs.meal_reminders_enabled is True
    assert prefs.hydration_reminder_time_minutes == 930
    assert DEFAULT_HYDRATION_REMINDER_MINUTES == 930
    as_dict = prefs.to_dict()
    assert as_dict["breakfast_reminder_enabled"] is True
    assert as_dict["lunch_reminder_enabled"] is True
    assert as_dict["dinner_reminder_enabled"] is True
    assert as_dict["hydration_reminder_time_minutes"] == 930
