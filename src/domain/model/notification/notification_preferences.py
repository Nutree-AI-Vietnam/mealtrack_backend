"""
Notification preferences domain model.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from src.domain.utils.timezone_utils import format_iso_utc, utc_now

from .enums import NotificationType

# Map notification types to their corresponding preference fields
NOTIFICATION_TYPE_TO_FIELD = {
    NotificationType.MEAL_REMINDER_BREAKFAST: "meal_reminders_enabled",
    NotificationType.MEAL_REMINDER_LUNCH: "meal_reminders_enabled",
    NotificationType.MEAL_REMINDER_DINNER: "meal_reminders_enabled",
    NotificationType.DAILY_SUMMARY: "daily_summary_enabled",
    NotificationType.HYDRATION_REMINDER_AFTERNOON: "hydration_reminders_enabled",
    NotificationType.HYDRATION_REMINDER_EVENING: "hydration_reminders_enabled",
}


@dataclass
class NotificationPreferences:
    """
    Domain model representing a user's notification preferences.
    """

    preferences_id: str  # UUID as string
    user_id: str  # UUID as string
    meal_reminders_enabled: bool = True
    daily_summary_enabled: bool = True
    hydration_reminders_enabled: bool = True
    breakfast_time_minutes: int | None = None  # minutes from midnight (0-1439)
    lunch_time_minutes: int | None = None
    dinner_time_minutes: int | None = None
    daily_summary_time_minutes: int | None = 1260  # 9:00 PM default
    language: str = "en"  # ISO 639-1; must be in ENABLED_APP_LOCALES
    created_at: datetime | None = None
    updated_at: datetime | None = None

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

        self._validate_time_minutes(
            self.breakfast_time_minutes, "breakfast_time_minutes"
        )
        self._validate_time_minutes(self.lunch_time_minutes, "lunch_time_minutes")
        self._validate_time_minutes(self.dinner_time_minutes, "dinner_time_minutes")
        self._validate_time_minutes(
            self.daily_summary_time_minutes, "daily_summary_time_minutes"
        )

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
            daily_summary_enabled=True,
            hydration_reminders_enabled=True,
            breakfast_time_minutes=510,  # 8:30 AM
            lunch_time_minutes=690,  # 11:30 AM
            dinner_time_minutes=1080,  # 6:00 PM
            daily_summary_time_minutes=1260,  # 9:00 PM
            language="en",
            created_at=utc_now(),
            updated_at=utc_now(),
        )

    def update_preferences(
        self,
        meal_reminders_enabled: bool | None = None,
        daily_summary_enabled: bool | None = None,
        hydration_reminders_enabled: bool | None = None,
        breakfast_time_minutes: int | None = None,
        lunch_time_minutes: int | None = None,
        dinner_time_minutes: int | None = None,
        daily_summary_time_minutes: int | None = None,
        language: str | None = None,
    ) -> "NotificationPreferences":
        """Update notification preferences with new values."""
        if breakfast_time_minutes is not None:
            self._validate_time_minutes(
                breakfast_time_minutes, "breakfast_time_minutes"
            )
        if lunch_time_minutes is not None:
            self._validate_time_minutes(lunch_time_minutes, "lunch_time_minutes")
        if dinner_time_minutes is not None:
            self._validate_time_minutes(dinner_time_minutes, "dinner_time_minutes")
        if daily_summary_time_minutes is not None:
            self._validate_time_minutes(
                daily_summary_time_minutes, "daily_summary_time_minutes"
            )

        return NotificationPreferences(
            preferences_id=self.preferences_id,
            user_id=self.user_id,
            meal_reminders_enabled=(
                meal_reminders_enabled
                if meal_reminders_enabled is not None
                else self.meal_reminders_enabled
            ),
            daily_summary_enabled=(
                daily_summary_enabled
                if daily_summary_enabled is not None
                else self.daily_summary_enabled
            ),
            hydration_reminders_enabled=(
                hydration_reminders_enabled
                if hydration_reminders_enabled is not None
                else self.hydration_reminders_enabled
            ),
            breakfast_time_minutes=(
                breakfast_time_minutes
                if breakfast_time_minutes is not None
                else self.breakfast_time_minutes
            ),
            lunch_time_minutes=(
                lunch_time_minutes
                if lunch_time_minutes is not None
                else self.lunch_time_minutes
            ),
            dinner_time_minutes=(
                dinner_time_minutes
                if dinner_time_minutes is not None
                else self.dinner_time_minutes
            ),
            daily_summary_time_minutes=(
                daily_summary_time_minutes
                if daily_summary_time_minutes is not None
                else self.daily_summary_time_minutes
            ),
            language=language if language is not None else self.language,
            created_at=self.created_at,
            updated_at=utc_now(),
        )

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
            "daily_summary_enabled": self.daily_summary_enabled,
            "hydration_reminders_enabled": self.hydration_reminders_enabled,
            "breakfast_time_minutes": self.breakfast_time_minutes,
            "lunch_time_minutes": self.lunch_time_minutes,
            "dinner_time_minutes": self.dinner_time_minutes,
            "daily_summary_time_minutes": self.daily_summary_time_minutes,
            "language": self.language,
            "created_at": format_iso_utc(self.created_at),
            "updated_at": format_iso_utc(self.updated_at),
        }
