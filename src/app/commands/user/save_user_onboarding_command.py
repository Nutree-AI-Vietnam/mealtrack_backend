"""
Save user onboarding command.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional, List

from src.app.events.base import Command


@dataclass
class SaveUserOnboardingCommand(Command):
    """Command to save user onboarding data."""

    user_id: str
    # Personal info
    age: int
    gender: str
    height_cm: float
    weight_kg: float

    # Goals
    job_type: str
    training_days_per_week: int
    training_minutes_per_session: int
    fitness_goal: str

    # Preferences - optional (may be empty from new onboarding flow)
    pain_points: Optional[List[str]] = None
    dietary_preferences: Optional[List[str]] = None

    # Attribution - optional (removed from onboarding flow)
    referral_sources: Optional[List[str]] = None

    # Meal preferences - optional (removed from onboarding flow, default 3)
    meals_per_day: int = 3

    # Optional fields
    body_fat_percentage: Optional[float] = None
    training_level: Optional[str] = None
    date_of_birth: Optional[date] = None
    target_weight_kg: Optional[float] = None

    # Onboarding redesign fields (NM-44)
    challenge_duration: Optional[str] = None
    training_types: Optional[List[str]] = None

    # Custom macro overrides (set during onboarding)
    custom_protein_g: Optional[float] = None
    custom_carbs_g: Optional[float] = None
    custom_fat_g: Optional[float] = None
    weight_loss_kg_per_week: Optional[float] = None
    weight_gain_kg_per_week: float | None = None
    pace_clinician_disclaimer: Optional[bool] = None
