"""Engagement toggles on NotificationPreferences (streak, comeback, progress, offers)."""

import pytest

from src.domain.model.notification.notification_preferences import (
    NotificationPreferences,
)

USER_ID = "00000000-0000-0000-0000-000000000002"
TOGGLES = (
    "streak_reminders_enabled",
    "reengagement_enabled",
    "progress_updates_enabled",
    "offers_enabled",
)
DEFAULTS = {
    "streak_reminders_enabled": True,
    "reengagement_enabled": True,
    "progress_updates_enabled": True,
    # Promotions need an opt-in (App Store guideline 4.5.4).
    "offers_enabled": False,
}


def _toggles(prefs: NotificationPreferences) -> dict[str, bool]:
    return {name: getattr(prefs, name) for name in TOGGLES}


@pytest.mark.unit
class TestEngagementToggleDefaults:
    def test_new_instance_defaults(self):
        prefs = NotificationPreferences(
            preferences_id="00000000-0000-0000-0000-000000000001", user_id=USER_ID
        )

        assert _toggles(prefs) == DEFAULTS

    def test_create_default_turns_offers_off_and_the_rest_on(self):
        prefs = NotificationPreferences.create_default(USER_ID)

        assert _toggles(prefs) == DEFAULTS

    def test_to_dict_exposes_every_toggle(self):
        prefs = NotificationPreferences.create_default(USER_ID)

        payload = prefs.to_dict()

        assert {name: payload[name] for name in TOGGLES} == DEFAULTS


@pytest.mark.unit
class TestEngagementToggleUpdates:
    @pytest.mark.parametrize("toggle", TOGGLES)
    def test_each_toggle_can_be_flipped_on_its_own(self, toggle):
        prefs = NotificationPreferences.create_default(USER_ID)

        updated = prefs.update_preferences(**{toggle: not DEFAULTS[toggle]})

        assert _toggles(updated) == {**DEFAULTS, toggle: not DEFAULTS[toggle]}

    def test_omitted_toggles_keep_their_stored_values(self):
        stored = NotificationPreferences.create_default(USER_ID).update_preferences(
            streak_reminders_enabled=False,
            reengagement_enabled=False,
            progress_updates_enabled=False,
            offers_enabled=True,
        )

        updated = stored.update_preferences(meal_reminders_enabled=False)

        assert updated.meal_reminders_enabled is False
        assert _toggles(updated) == {
            "streak_reminders_enabled": False,
            "reengagement_enabled": False,
            "progress_updates_enabled": False,
            "offers_enabled": True,
        }

    def test_new_toggles_do_not_disturb_the_existing_ones(self):
        stored = NotificationPreferences.create_default(USER_ID).update_preferences(
            hydration_reminders_enabled=False, daily_summary_enabled=False
        )

        updated = stored.update_preferences(offers_enabled=True)

        assert updated.offers_enabled is True
        assert updated.hydration_reminders_enabled is False
        assert updated.daily_summary_enabled is False
        assert updated.meal_reminders_enabled is True
