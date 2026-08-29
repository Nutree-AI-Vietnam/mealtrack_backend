"""
Notification request schemas for notification preferences management.
"""

from typing import Optional

from pydantic import BaseModel, Field, field_validator

# Supported notification languages (TODO: add more locales)
SUPPORTED_NOTIFICATION_LANGUAGES = {"en", "vi"}


class NotificationPreferencesUpdateRequest(BaseModel):
    """Request to update notification preferences."""

    meal_reminders_enabled: Optional[bool] = Field(
        None, description="Enable/disable meal reminders"
    )
    daily_summary_enabled: Optional[bool] = Field(
        None, description="Enable/disable daily summary notifications"
    )
    hydration_reminders_enabled: Optional[bool] = Field(
        None, description="Enable/disable hydration reminder notifications"
    )

    # Meal timing (minutes from midnight: 0-1439)
    breakfast_time_minutes: Optional[int] = Field(
        None,
        ge=0,
        le=1439,
        description="Breakfast reminder time (minutes from midnight)",
    )
    lunch_time_minutes: Optional[int] = Field(
        None, ge=0, le=1439, description="Lunch reminder time (minutes from midnight)"
    )
    dinner_time_minutes: Optional[int] = Field(
        None, ge=0, le=1439, description="Dinner reminder time (minutes from midnight)"
    )

    # Daily summary timing
    daily_summary_time_minutes: Optional[int] = Field(
        None,
        ge=0,
        le=1439,
        description="Daily summary time (minutes from midnight)",
    )

    # Preferred notification language
    language: Optional[str] = Field(
        None,
        description="Preferred notification language (ISO 639-1 code, e.g., 'en', 'vi')",
    )

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: Optional[str]) -> Optional[str]:
        """Validate notification language is supported."""
        if v is not None and v.lower() not in SUPPORTED_NOTIFICATION_LANGUAGES:
            supported = ", ".join(sorted(SUPPORTED_NOTIFICATION_LANGUAGES))
            raise ValueError(
                f"Unsupported notification language: '{v}'. Supported languages: {supported}"
            )
        return v.lower() if v else None
