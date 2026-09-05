"""
User management response schemas for Firebase integration.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from src.domain.utils.timezone_utils import utc_now

from ..common.auth_enums import AuthProviderEnum


class SubscriptionInfo(BaseModel):
    """Subscription information."""

    product_id: str = Field(
        ..., description="Subscription product ID (premium_monthly or premium_yearly)"
    )
    status: str = Field(
        ...,
        description="Subscription status (active, cancelled, expired, billing_issue)",
    )
    expires_at: Optional[datetime] = Field(
        None, description="Subscription expiration date"
    )
    is_monthly: bool = Field(..., description="Whether this is a monthly subscription")
    is_yearly: bool = Field(..., description="Whether this is a yearly subscription")
    platform: str = Field(..., description="Platform (ios, android, web)")


class UserProfileResponse(BaseModel):
    """Response containing user profile information."""

    id: str = Field(..., description="User internal ID")
    firebase_uid: str = Field(..., description="Firebase user unique identifier")
    email: EmailStr = Field(..., description="User email address")
    username: str = Field(..., description="Username")
    first_name: Optional[str] = Field(None, description="First name")
    last_name: Optional[str] = Field(None, description="Last name")
    phone_number: Optional[str] = Field(None, description="Phone number")
    display_name: Optional[str] = Field(None, description="Display name")
    photo_url: Optional[str] = Field(None, description="Profile photo URL")
    provider: AuthProviderEnum = Field(..., description="Authentication provider")
    is_active: bool = Field(..., description="Whether user account is active")
    onboarding_completed: bool = Field(
        ..., description="Whether user completed onboarding"
    )
    last_accessed: Optional[datetime] = Field(
        None, description="Last accessed timestamp"
    )
    created_at: datetime = Field(..., description="Account creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    # Subscription fields
    has_subscription: bool = Field(
        ..., description="Whether user has active subscription"
    )
    subscription: Optional[SubscriptionInfo] = Field(
        None, description="Active subscription details if any"
    )


class UserSyncResponse(BaseModel):
    """Response from user sync operation."""

    user: UserProfileResponse = Field(..., description="User profile data")
    created: bool = Field(..., description="Whether user was newly created")
    updated: bool = Field(..., description="Whether existing user was updated")
    message: str = Field(..., description="Operation result message")


class UserStatusResponse(BaseModel):
    """Response containing user status information."""

    firebase_uid: str = Field(..., description="Firebase user unique identifier")
    onboarding_completed: bool = Field(
        ..., description="Whether user completed onboarding"
    )
    is_active: bool = Field(..., description="Whether user account is active")
    last_accessed: Optional[datetime] = Field(
        None, description="Last accessed timestamp"
    )


class UserUpdateResponse(BaseModel):
    """Response from user update operations."""

    firebase_uid: str = Field(..., description="Firebase user unique identifier")
    updated: bool = Field(..., description="Whether update was successful")
    message: str = Field(..., description="Operation result message")
    timestamp: datetime = Field(default_factory=utc_now, description="Update timestamp")


class OnboardingCompletionResponse(BaseModel):
    """Response from onboarding completion operation."""

    firebase_uid: str = Field(..., description="Firebase user unique identifier")
    onboarding_completed: bool = Field(
        ..., description="Current onboarding completion status"
    )
    updated: bool = Field(..., description="Whether update was successful")
    message: str = Field(..., description="Operation result message")


class UserMetricsResponse(BaseModel):
    """Response containing user's current metrics for settings display."""

    user_id: str = Field(..., description="User internal ID")
    age: int = Field(..., description="User age")
    gender: str = Field(..., description="User gender")
    height_cm: float = Field(..., description="Height in centimeters")
    weight_kg: float = Field(..., description="Current weight in kilograms")
    body_fat_percentage: Optional[float] = Field(
        None, description="Body fat percentage"
    )
    job_type: str = Field(..., description="Job type: desk, on_feet, physical")
    training_days_per_week: int = Field(..., description="Training days per week (0-7)")
    training_minutes_per_session: int = Field(
        ..., description="Training minutes per session (15-180)"
    )
    training_level: Optional[str] = Field(
        None, description="Training level: beginner, intermediate, advanced"
    )
    fitness_goal: str = Field(..., description="Current fitness goal")
    target_weight_kg: Optional[float] = Field(
        None, description="Target weight in kilograms"
    )
    goal_start_weight_kg: Optional[float] = Field(
        None, description="Weight when goal journey started"
    )
    goal_started_at: Optional[datetime] = Field(
        None, description="Timestamp when goal journey started"
    )
    journey_progress_seed_percent: float = Field(
        0.0, description="Starting journey percent granted from historical actions"
    )
    updated_at: datetime = Field(..., description="Last update timestamp")


class UserDeleteResponse(BaseModel):
    """Response from user account deletion operation."""

    firebase_uid: str = Field(..., description="Firebase user unique identifier")
    deleted: bool = Field(..., description="Whether deletion was successful")
    firebase_deleted: bool = Field(
        True,
        description=(
            "Whether the Firebase auth account was deleted; false means an "
            "orphaned account remains and a retry is pending"
        ),
    )
    tokens_revoked: bool = Field(
        True, description="Whether Firebase refresh tokens were revoked"
    )
    firebase_cleanup_queued: bool = Field(
        False, description="Whether Firebase revoke/delete work was queued"
    )
    message: str = Field(..., description="Operation result message")
