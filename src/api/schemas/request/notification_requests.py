"""
Notification request schemas for notification preferences management.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from src.domain.constants.languages import ENABLED_APP_LOCALES


class FcmTokenRegisterRequest(BaseModel):
    """Client push-token registration (local and remote)."""

    fcm_token: str = Field(..., min_length=1, max_length=4096)
    device_type: Literal["ios", "android"]
    timezone: str | None = Field(default=None, max_length=64)


class FcmTokenDeleteRequest(BaseModel):
    fcm_token: str = Field(..., min_length=1, max_length=4096)


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
        description="Preferred notification language (ISO 639-1 code, e.g., 'en', 'ja')",
    )

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: Optional[str]) -> Optional[str]:
        """Validate notification language is supported."""
        if v is not None and v.lower() not in ENABLED_APP_LOCALES:
            supported = ", ".join(sorted(ENABLED_APP_LOCALES))
            raise ValueError(
                f"Unsupported notification language: '{v}'. Supported languages: {supported}"
            )
        return v.lower() if v else None
