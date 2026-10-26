"""
Notification response schemas for notification preferences management.
"""

from typing import Optional

from pydantic import BaseModel, Field


class FcmTokenActionResponse(BaseModel):
    success: bool
    message: str


class NotificationPreferencesResponse(BaseModel):
    """Response containing notification preferences."""

    meal_reminders_enabled: bool = Field(
        ..., description="Whether meal reminders are enabled"
    )
    daily_summary_enabled: bool = Field(
        ..., description="Whether daily summary notifications are enabled"
    )
    hydration_reminders_enabled: bool = Field(
        True, description="Whether hydration reminder notifications are enabled"
    )
    streak_reminders_enabled: bool = Field(
        True, description="Whether streak alerts (streak saver, win the day) are on"
    )
    reengagement_enabled: bool = Field(
        True, description="Whether comeback nudges are on"
    )
    progress_updates_enabled: bool = Field(
        True, description="Whether progress notes and recaps are on"
    )
    offers_enabled: bool = Field(
        False,
        description="Whether the user opted in to offers and new-feature news",
    )

    # Meal timing (minutes from midnight: 0-1439)
    breakfast_time_minutes: Optional[int] = Field(
        None, description="Breakfast reminder time (minutes from midnight)"
    )
    lunch_time_minutes: Optional[int] = Field(
        None, description="Lunch reminder time (minutes from midnight)"
    )
    dinner_time_minutes: Optional[int] = Field(
        None, description="Dinner reminder time (minutes from midnight)"
    )

    # Daily summary timing
    daily_summary_time_minutes: Optional[int] = Field(
        None, description="Daily summary time (minutes from midnight)"
    )

    # Preferred notification language
    language: str = Field(
        "en", description="Preferred notification language (ISO 639-1)"
    )


class NotificationPreferencesUpdateResponse(BaseModel):
    """Response for notification preferences update."""

    success: bool = Field(..., description="Whether the update was successful")
    preferences: NotificationPreferencesResponse = Field(
        ..., description="Updated notification preferences"
    )
