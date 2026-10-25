"""
Users API endpoints - Firebase integration for user management.
Handles user authentication sync, profile retrieval, and status management.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from src.api.dependencies.auth import (
    get_current_user_id,
    verify_firebase_token,
    verify_firebase_uid_ownership,
)
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.schemas.request.user_requests import (
    UserSyncRequest,
    UserUpdateLastAccessedRequest,
)
from src.api.schemas.response.user_responses import (
    OnboardingCompletionResponse,
    UserDeleteResponse,
    UserProfileResponse,
    UserStatusResponse,
    UserSyncResponse,
    UserUpdateResponse,
)
from src.app.commands.user import (
    CompleteOnboardingCommand,
    DeleteUserCommand,
    UpdateLanguageCommand,
    UpdateTimezoneCommand,
    UpdateWeeklyAutoAdjustCommand,
)
from src.app.commands.user.sync_user_command import (
    SyncUserCommand,
    UpdateUserLastAccessedCommand,
)
from src.app.queries.user.get_user_by_firebase_uid_query import (
    GetUserByFirebaseUidQuery,
)
from src.app.queries.user.get_user_onboarding_status_query import (
    GetUserOnboardingStatusQuery,
)
from src.app.queries.user.get_weekly_auto_adjust_query import (
    GetWeeklyAutoAdjustQuery,
)
from src.domain.exceptions.firebase_identity_exceptions import (
    FirebaseIdentityConflictError,
)
from src.infra.event_bus import EventBus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/users", tags=["Users"])


class UpdateLanguageRequest(BaseModel):
    """Request body for language preference update."""

    language_code: str


class UpdateTimezoneRequest(BaseModel):
    """Request body for timezone update."""

    timezone: str


class UpdateWeeklyAutoAdjustRequest(BaseModel):
    """Request body for leftover-split daily adjustment."""

    enabled: bool


@router.post("/sync", response_model=UserSyncResponse)
async def sync_user_from_firebase(
    request: UserSyncRequest,
    http_request: Request,
    token: dict = Depends(verify_firebase_token),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Sync user data from Firebase authentication.

    Creates a new user if they don't exist, or updates existing user data.
    This endpoint is called automatically when a user signs in through Firebase.

    - **firebase_uid**: Firebase user unique identifier
    - **email**: User email address
    - **phone_number**: User phone number (optional)
    - **display_name**: User display name from Firebase (optional)
    - **photo_url**: User profile photo URL (optional)
    - **provider**: Authentication provider (phone, google)
    """
    if token.get("uid") != request.firebase_uid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: you can only sync your own account",
        )

    logger.info(
        f"Starting user sync for firebase_uid: {request.firebase_uid}",
        extra={
            "firebase_uid": request.firebase_uid,
            "provider": request.provider,
            "has_email": bool(request.email),
            "has_phone": bool(request.phone_number),
        },
    )
    # Create sync command
    # Extract language from Accept-Language header for new user creation
    accept_language = http_request.headers.get("accept-language", "")
    language_code = accept_language.split(",")[0].split("-")[0].strip() or None

    command = SyncUserCommand(
        firebase_uid=request.firebase_uid,
        email=request.email,
        phone_number=request.phone_number,
        display_name=request.display_name,
        photo_url=request.photo_url,
        provider=request.provider,
        username=request.username,
        first_name=request.first_name,
        last_name=request.last_name,
        language_code=language_code,
    )

    # Send command
    try:
        result = await event_bus.send(command)
    except FirebaseIdentityConflictError as exc:
        logger.warning("User sync rejected because Firebase identity conflicts")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account identity could not be verified",
        ) from exc

    # Map result to response
    user_data = result["user"]

    logger.info(
        f"User sync completed for firebase_uid: {request.firebase_uid}",
        extra={
            "firebase_uid": request.firebase_uid,
            "user_created": result["created"],
            "user_updated": result["updated"],
        },
    )

    return UserSyncResponse(
        user=UserProfileResponse(**user_data),
        created=result["created"],
        updated=result["updated"],
        message=result["message"],
    )


@router.get("/firebase/{firebase_uid}", response_model=UserProfileResponse)
async def get_user_by_firebase_uid(
    firebase_uid: str = Depends(verify_firebase_uid_ownership),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Get user profile by Firebase UID.

    Retrieves complete user profile information using Firebase UID.
    This is the primary way to get user data after Firebase authentication.

    - **firebase_uid**: Firebase user unique identifier
    """
    # Create query
    query = GetUserByFirebaseUidQuery(firebase_uid=firebase_uid)

    # Send query
    result = await event_bus.send(query)

    # Return user profile response
    return UserProfileResponse(**result)


@router.get("/firebase/{firebase_uid}/status", response_model=UserStatusResponse)
async def get_user_onboarding_status(
    firebase_uid: str = Depends(verify_firebase_uid_ownership),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Get user's onboarding status by Firebase UID.

    Returns minimal user status information for onboarding flow decisions.
    Used by the mobile app to determine if user needs to complete onboarding.

    - **firebase_uid**: Firebase user unique identifier
    """
    # Create query
    query = GetUserOnboardingStatusQuery(firebase_uid=firebase_uid)

    # Send query
    result = await event_bus.send(query)

    # Return status response
    return UserStatusResponse(**result)


@router.put("/firebase/{firebase_uid}/last-accessed", response_model=UserUpdateResponse)
async def update_user_last_accessed(
    request: UserUpdateLastAccessedRequest,
    firebase_uid: str = Depends(verify_firebase_uid_ownership),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Update user's last accessed timestamp.

    Updates the last_accessed field for activity tracking and analytics.
    Called periodically by the mobile app to track user engagement.

    - **firebase_uid**: Firebase user unique identifier
    - **last_accessed**: Timestamp of last access (optional, defaults to now)
    """
    # Create command
    command = UpdateUserLastAccessedCommand(
        firebase_uid=firebase_uid, last_accessed=request.last_accessed
    )

    # Send command
    result = await event_bus.send(command)

    # Return update response
    return UserUpdateResponse(**result)


@router.put(
    "/firebase/{firebase_uid}/onboarding/complete",
    response_model=OnboardingCompletionResponse,
)
async def complete_onboarding(
    firebase_uid: str = Depends(verify_firebase_uid_ownership),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Mark user onboarding as completed.

    Sets the user's onboarding status to completed if it's currently false.
    This endpoint is called when the user finishes the onboarding flow in the mobile app.

    - **firebase_uid**: Firebase user unique identifier
    """
    # Create command
    command = CompleteOnboardingCommand(firebase_uid=firebase_uid)

    # Send command
    result = await event_bus.send(command)

    # Return completion response
    return OnboardingCompletionResponse(**result)


@router.put("/timezone")
async def update_timezone(
    request: UpdateTimezoneRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Update user's timezone. Called on app open/resume for accurate meal type detection.

    - **timezone**: IANA timezone identifier (e.g., "Asia/Saigon", "America/New_York")
    """
    command = UpdateTimezoneCommand(user_id=user_id, timezone=request.timezone)
    result = await event_bus.send(command)
    return result


@router.patch("/language")
async def update_language(
    request: UpdateLanguageRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Update user's preferred language.

    - **language_code**: ISO 639-1 code (en, vi, es, fr, de, ja, zh)
    """
    command = UpdateLanguageCommand(
        user_id=user_id, language_code=request.language_code
    )
    result = await event_bus.send(command)
    return result


@router.get("/weekly-auto-adjust")
async def get_weekly_auto_adjust(
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """Return whether leftover calories auto-adjust today's target."""
    result = await event_bus.send(GetWeeklyAutoAdjustQuery(user_id=user_id))
    return result


@router.patch("/weekly-auto-adjust")
async def update_weekly_auto_adjust(
    request: UpdateWeeklyAutoAdjustRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """Enable or disable leftover-split daily targets. Enabled by default."""
    command = UpdateWeeklyAutoAdjustCommand(
        user_id=user_id, enabled=request.enabled
    )
    result = await event_bus.send(command)
    return result


@router.delete("/firebase/{firebase_uid}", response_model=UserDeleteResponse)
async def delete_user_account(
    firebase_uid: str = Depends(verify_firebase_uid_ownership),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Delete user account and queue Firebase cleanup.

    Performs complete account deletion:
    - Soft deletes user in database (sets is_active=False)
    - Anonymizes user data for GDPR compliance
    - Queues Firebase token revocation and account deletion after the SQL commit

    This action cannot be undone. All user data will be anonymized and
    the account will be marked as inactive.

    - **firebase_uid**: Firebase user unique identifier
    """
    logger.info(
        f"Starting account deletion for firebase_uid: {firebase_uid}",
        extra={"firebase_uid": firebase_uid},
    )
    # Create delete command
    command = DeleteUserCommand(firebase_uid=firebase_uid)

    # Send command
    result = await event_bus.send(command)

    logger.info(
        f"Account deletion completed for firebase_uid: {firebase_uid}",
        extra={"firebase_uid": firebase_uid, "deleted": result["deleted"]},
    )

    # Return deletion response
    return UserDeleteResponse(**result)
