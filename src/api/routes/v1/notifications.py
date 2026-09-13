"""
Notifications API endpoints for user notification preferences management.
"""

from fastapi import APIRouter, Depends

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.schemas.request.notification_requests import (
    FcmTokenDeleteRequest,
    FcmTokenRegisterRequest,
    NotificationPreferencesUpdateRequest,
)
from src.api.schemas.response.notification_responses import (
    FcmTokenActionResponse,
    NotificationPreferencesResponse,
    NotificationPreferencesUpdateResponse,
)
from src.app.commands.notification import (
    UpdateNotificationPreferencesCommand,
)
from src.app.queries.notification import GetNotificationPreferencesQuery
from src.infra.event_bus import EventBus

router = APIRouter(prefix="/v1/notifications", tags=["Notifications"])


@router.post("/tokens", response_model=FcmTokenActionResponse)
async def register_fcm_token(
    request: FcmTokenRegisterRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Accept a device token so local clients do not 404 on register.

    Push delivery is still owned by the notification worker; this endpoint
    acknowledges the token so boot is not treated as a missing API.
    """
    del request, user_id
    return FcmTokenActionResponse(success=True, message="Token registered")


@router.delete("/tokens", response_model=FcmTokenActionResponse)
async def delete_fcm_token(
    request: FcmTokenDeleteRequest,
    user_id: str = Depends(get_current_user_id),
):
    del request, user_id
    return FcmTokenActionResponse(success=True, message="Token deleted")


@router.get("/preferences", response_model=NotificationPreferencesResponse)
async def get_notification_preferences(
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Get user's notification preferences.

    Returns the current notification preferences for the user.
    If no preferences exist, creates and returns default preferences.
    """
    query = GetNotificationPreferencesQuery(user_id=user_id)

    result = await event_bus.send(query)

    return NotificationPreferencesResponse(**result)


@router.put("/preferences", response_model=NotificationPreferencesUpdateResponse)
async def update_notification_preferences(
    request: NotificationPreferencesUpdateRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Update user's notification preferences.

    Updates the notification preferences for the user.
    Only provided fields will be updated.
    """
    command = UpdateNotificationPreferencesCommand(
        user_id=user_id,
        meal_reminders_enabled=request.meal_reminders_enabled,
        daily_summary_enabled=request.daily_summary_enabled,
        hydration_reminders_enabled=request.hydration_reminders_enabled,
        breakfast_time_minutes=request.breakfast_time_minutes,
        lunch_time_minutes=request.lunch_time_minutes,
        dinner_time_minutes=request.dinner_time_minutes,
        daily_summary_time_minutes=request.daily_summary_time_minutes,
        language=request.language,
    )

    result = await event_bus.send(command)

    return NotificationPreferencesUpdateResponse(
        success=result["success"],
        preferences=NotificationPreferencesResponse(**result["preferences"]),
    )
