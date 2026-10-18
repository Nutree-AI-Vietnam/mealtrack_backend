"""
Domain models for User and UserProfile.

These models are plain Python objects that represent the core business logic
and are independent of the database or any other infrastructure.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from src.domain.model.auth.auth_provider import AuthProvider
from src.domain.model.base import BaseDomainModel
from src.domain.utils.timezone_utils import utc_now


@dataclass(kw_only=True)
class UserProfileDomainModel(BaseDomainModel):
    """Domain model for a user's profile."""

    user_id: UUID
    age: int
    gender: str
    height_cm: float
    weight_kg: float
    job_type: str
    training_days_per_week: int
    training_minutes_per_session: int
    fitness_goal: str
    meals_per_day: int
    is_current: bool = True
    body_fat_percentage: float | None = None
    date_of_birth: date | None = None
    target_weight_kg: float | None = None
    snacks_per_day: int = 1
    dietary_preferences: list[str] = field(default_factory=list)
    health_conditions: list[str] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    pain_points: list[str] = field(default_factory=list)
    training_level: str | None = None
    referral_sources: list[str] = field(default_factory=list)
    challenge_duration: str | None = None
    training_types: list[str] | None = None
    custom_protein_g: float | None = None
    custom_carbs_g: float | None = None
    custom_fat_g: float | None = None
    goal_start_weight_kg: float | None = None
    goal_started_at: datetime | None = None
    journey_progress_seed_percent: float = 0.0
    daily_water_goal_ml: int | None = None
    profile_target_revision: int = 1


@dataclass(kw_only=True)
class UserDomainModel(BaseDomainModel):
    """Domain model for a User."""

    firebase_uid: str
    email: str
    username: str
    password_hash: str
    provider: AuthProvider
    is_active: bool = True
    onboarding_completed: bool = False
    last_accessed: datetime = field(default_factory=utc_now)
    timezone: str = "UTC"
    first_name: str | None = None
    last_name: str | None = None
    phone_number: str | None = None
    display_name: str | None = None
    photo_url: str | None = None
    deleted_at: datetime | None = None
    language_code: str | None = None
    profiles: list[UserProfileDomainModel] = field(default_factory=list)

    @property
    def current_profile(self) -> UserProfileDomainModel | None:
        """Get the current active profile."""
        return next((p for p in self.profiles if p.is_current), None)

    def has_active_subscription(self) -> bool:
        """
        Check if user has active subscription.
        Note: This is a placeholder as subscription is not yet a domain model.
        """
        return False
