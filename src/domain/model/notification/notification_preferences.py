"""
Notification preferences domain model.
"""

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from src.domain.utils.timezone_utils import format_iso_utc, utc_now

from .enums import NotificationType

# Meal reminder times a user starts with, in minutes from midnight
DEFAULT_MEAL_REMINDER_MINUTES = {
    "breakfast": 510,  # 8:30 AM
    "lunch": 690,  # 11:30 AM
    "dinner": 1080,  # 6:00 PM
}

# The one daily water reminder, in minutes from midnight
DEFAULT_HYDRATION_REMINDER_MINUTES = 930  # 3:30 PM

# Map notification types to their corresponding preference fields
NOTIFICATION_TYPE_TO_FIELD = {
    NotificationType.MEAL_REMINDER_BREAKFAST: "breakfast_reminder_enabled",
    NotificationType.MEAL_REMINDER_LUNCH: "lunch_reminder_enabled",
    NotificationType.MEAL_REMINDER_DINNER: "dinner_reminder_enabled",
    NotificationType.DAILY_SUMMARY: "daily_summary_enabled",
    NotificationType.HYDRATION_REMINDER_AFTERNOON: "hydration_reminders_enabled",
    NotificationType.HYDRATION_REMINDER_EVENING: "hydration_reminders_enabled",
}

_TIME_FIELDS = (
    "breakfast_time_minutes",
    "lunch_time_minutes",
    "dinner_time_minutes",
    "daily_summary_time_minutes",
    "hydration_reminder_time_minutes",
)


@dataclass
class NotificationPreferences:
    """
    Domain model representing a user's notification preferences.

    `meal_reminders_enabled` is what older apps read and send. After any
    update it is true while at least one meal switch is on.
    """

    preferences_id: str  # UUID as string
    user_id: str  # UUID as string
    meal_reminders_enabled: bool = True
    daily_summary_enabled: bool = True
    hydration_reminders_enabled: bool = True
    # Engagement toggles for notifications the device plans (streak saver,
    # lapse ladder, recaps). Offers stay off until the user opts in, per App
    # Store guideline 4.5.4.
    streak_reminders_enabled: bool = True
    reengagement_enabled: bool = True
    progress_updates_enabled: bool = True
    offers_enabled: bool = False
    breakfast_time_minutes: int | None = None  # minutes from midnight (0-1439)
    lunch_time_minutes: int | None = None
    dinner_time_minutes: int | None = None
    daily_summary_time_minutes: int | None = 1260  # 9:00 PM default
    language: str = "en"  # ISO 639-1; must be in ENABLED_APP_LOCALES
    created_at: datetime | None = None
    updated_at: datetime | None = None
    breakfast_reminder_enabled: bool = True
    lunch_reminder_enabled: bool = True
    dinner_reminder_enabled: bool = True
    hydration_reminder_time_minutes: int = DEFAULT_HYDRATION_REMINDER_MINUTES

    def __post_init__(self):
        """Validate invariants."""
        try:
            uuid.UUID(self.preferences_id)
        except ValueError as e:
            raise ValueError(
                f"Invalid UUID format for preferences_id: {self.preferences_id}"
            ) from e

        try:
            uuid.UUID(self.user_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format for user_id: {self.user_id}") from e

        for field_name in _TIME_FIELDS:
            self._validate_time_minutes(getattr(self, field_name), field_name)

    def _validate_time_minutes(self, time_minutes: int | None, field_name: str):
        """Validate time in minutes from midnight."""
        if time_minutes is not None and (time_minutes < 0 or time_minutes >= 1440):
            raise ValueError(
                f"{field_name} must be between 0 and 1439 (minutes from midnight)"
            )

    @classmethod
    def create_default(cls, user_id: str) -> "NotificationPreferences":
        """Factory method to create default notification preferences."""
        return cls(
            preferences_id=str(uuid.uuid4()),
            user_id=user_id,
            meal_reminders_enabled=True,
            breakfast_reminder_enabled=True,
            lunch_reminder_enabled=True,
            dinner_reminder_enabled=True,
            daily_summary_enabled=True,
            hydration_reminders_enabled=True,
            streak_reminders_enabled=True,
            reengagement_enabled=True,
            progress_updates_enabled=True,
            offers_enabled=False,
            breakfast_time_minutes=DEFAULT_MEAL_REMINDER_MINUTES["breakfast"],
            lunch_time_minutes=DEFAULT_MEAL_REMINDER_MINUTES["lunch"],
            dinner_time_minutes=DEFAULT_MEAL_REMINDER_MINUTES["dinner"],
            daily_summary_time_minutes=1260,  # 9:00 PM
            hydration_reminder_time_minutes=DEFAULT_HYDRATION_REMINDER_MINUTES,
            language="en",
            created_at=utc_now(),
            updated_at=utc_now(),
        )

    def update_preferences(
        self,
        meal_reminders_enabled: bool | None = None,
        daily_summary_enabled: bool | None = None,
        hydration_reminders_enabled: bool | None = None,
        streak_reminders_enabled: bool | None = None,
        reengagement_enabled: bool | None = None,
        progress_updates_enabled: bool | None = None,
        offers_enabled: bool | None = None,
        breakfast_time_minutes: int | None = None,
        lunch_time_minutes: int | None = None,
        dinner_time_minutes: int | None = None,
        daily_summary_time_minutes: int | None = None,
        language: str | None = None,
        breakfast_reminder_enabled: bool | None = None,
        lunch_reminder_enabled: bool | None = None,
        dinner_reminder_enabled: bool | None = None,
        hydration_reminder_time_minutes: int | None = None,
    ) -> "NotificationPreferences":
        """Return a copy with the given values; None keeps the current value."""
        requested = {
            "daily_summary_enabled": daily_summary_enabled,
            "hydration_reminders_enabled": hydration_reminders_enabled,
            "streak_reminders_enabled": streak_reminders_enabled,
            "reengagement_enabled": reengagement_enabled,
            "progress_updates_enabled": progress_updates_enabled,
            "offers_enabled": offers_enabled,
            "breakfast_time_minutes": breakfast_time_minutes,
            "lunch_time_minutes": lunch_time_minutes,
            "dinner_time_minutes": dinner_time_minutes,
            "daily_summary_time_minutes": daily_summary_time_minutes,
            "hydration_reminder_time_minutes": hydration_reminder_time_minutes,
            "language": language,
        }
        changes: dict[str, Any] = {
            name: value for name, value in requested.items() if value is not None
        }
        breakfast, lunch, dinner = self._meal_switches_after_update(
            meal_reminders_enabled,
            (
                breakfast_reminder_enabled,
                lunch_reminder_enabled,
                dinner_reminder_enabled,
            ),
        )
        # replace() runs __post_init__, which validates every time field.
        return replace(
            self,
            **changes,
            breakfast_reminder_enabled=breakfast,
            lunch_reminder_enabled=lunch,
            dinner_reminder_enabled=dinner,
            meal_reminders_enabled=breakfast or lunch or dinner,
            updated_at=utc_now(),
        )

    def _meal_switches_after_update(
        self,
        meal_reminders_enabled: bool | None,
        per_meal: tuple[bool | None, bool | None, bool | None],
    ) -> tuple[bool, bool, bool]:
        """Resolve the three meal switches for an update.

        Any per-meal value wins and the legacy flag is ignored. Older apps send
        only the legacy flag, and send it with every save, so it changes the
        switches only when it differs from what they currently add up to.
        """
        stored = (
            self.breakfast_reminder_enabled,
            self.lunch_reminder_enabled,
            self.dinner_reminder_enabled,
        )
        if any(value is not None for value in per_meal):
            breakfast, lunch, dinner = (
                old if new is None else new
                for new, old in zip(per_meal, stored, strict=True)
            )
            return breakfast, lunch, dinner
        if meal_reminders_enabled is not None and meal_reminders_enabled != any(stored):
            return (meal_reminders_enabled,) * 3
        return stored

    def is_notification_type_enabled(self, notification_type: NotificationType) -> bool:
        """Check if a specific notification type is enabled."""
        field_name = NOTIFICATION_TYPE_TO_FIELD.get(notification_type)
        return getattr(self, field_name, False) if field_name else False

    def get_meal_reminder_time(self, meal_type: str) -> int | None:
        """Get the reminder time in minutes for a specific meal type."""
        if meal_type == "breakfast":
            return self.breakfast_time_minutes
        elif meal_type == "lunch":
            return self.lunch_time_minutes
        elif meal_type == "dinner":
            return self.dinner_time_minutes
        else:
            return None

    def to_dict(self) -> dict:
        """Convert to dictionary format."""
        return {
            "preferences_id": self.preferences_id,
            "user_id": self.user_id,
            "meal_reminders_enabled": self.meal_reminders_enabled,
            "breakfast_reminder_enabled": self.breakfast_reminder_enabled,
            "lunch_reminder_enabled": self.lunch_reminder_enabled,
            "dinner_reminder_enabled": self.dinner_reminder_enabled,
            "daily_summary_enabled": self.daily_summary_enabled,
            "hydration_reminders_enabled": self.hydration_reminders_enabled,
            "streak_reminders_enabled": self.streak_reminders_enabled,
            "reengagement_enabled": self.reengagement_enabled,
            "progress_updates_enabled": self.progress_updates_enabled,
            "offers_enabled": self.offers_enabled,
            "breakfast_time_minutes": self.breakfast_time_minutes,
            "lunch_time_minutes": self.lunch_time_minutes,
            "dinner_time_minutes": self.dinner_time_minutes,
            "daily_summary_time_minutes": self.daily_summary_time_minutes,
            "hydration_reminder_time_minutes": self.hydration_reminder_time_minutes,
            "language": self.language,
            "created_at": format_iso_utc(self.created_at),
            "updated_at": format_iso_utc(self.updated_at),
        }
