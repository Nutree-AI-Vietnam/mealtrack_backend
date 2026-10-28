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
        ..., description="Whether any meal reminder is enabled"
    )
    breakfast_reminder_enabled: bool = Field(
        True, description="Whether the breakfast reminder is enabled"
    )
    lunch_reminder_enabled: bool = Field(
        True, description="Whether the lunch reminder is enabled"
    )
    dinner_reminder_enabled: bool = Field(
        True, description="Whether the dinner reminder is enabled"
    )
    daily_summary_enabled: bool = Field(
        ..., description="Whether daily summary notifications are enabled"
    )
    hydration_reminders_enabled: bool = Field(
        True, description="Whether hydration reminder notifications are enabled"
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

    # Water reminder timing
    hydration_reminder_time_minutes: int = Field(
        930, description="Water reminder time (minutes from midnight)"
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
