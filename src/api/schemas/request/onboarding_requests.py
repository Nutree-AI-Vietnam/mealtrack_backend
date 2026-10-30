from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class TrainingLevelEnum(StrEnum):
    """Enum for training experience levels."""

    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"


class JobTypeEnum(StrEnum):
    """Enum for job types based on daily movement requirements."""

    desk = "desk"
    on_feet = "on_feet"
    physical = "physical"


class OnboardingCompleteRequest(BaseModel):
    """Complete onboarding data request for saving to database."""

    # Personal info - REQUIRED (DOB replaces age — age computed server-side)
    birth_year: int = Field(..., ge=1900, le=2100)
    birth_month: int = Field(..., ge=1, le=12)
    birth_day: int = Field(..., ge=1, le=31)
    gender: str = Field(..., description="male/female")
    height: float = Field(..., gt=0, description="Height in cm")
    weight: float = Field(..., gt=0, description="Weight in kg")
    body_fat_percentage: float | None = Field(None, ge=0, le=100)

    # Activity and goals - REQUIRED
    job_type: str = Field(..., description="desk/on_feet/physical")
    training_days_per_week: int = Field(
        ..., ge=0, le=7, description="Days of training per week"
    )
    training_minutes_per_session: int = Field(
        ..., ge=0, le=180, description="Minutes per training session"
    )
    goal: str = Field(..., description="bulk/cut/maintain/recomp")

    # Training level - OPTIONAL (resistance training experience)
    training_level: str | None = Field(
        None, description="beginner/intermediate/advanced"
    )

    # User experience
    pain_points: list[str] = Field(default_factory=list, description="User pain points")
    dietary_preferences: list[str] = Field(
        default_factory=list, description="Dietary preferences"
    )

    # Meal preferences - OPTIONAL (default 3, screen removed in onboarding redesign)
    meals_per_day: int = Field(3, ge=1, le=10)

    # Target weight - OPTIONAL
    target_weight_kg: float | None = Field(None, gt=0)

    # Attribution - OPTIONAL (screen removed in onboarding redesign)
    referral_sources: list[str] = Field(
        default_factory=list, description="How user heard about us"
    )

    # Onboarding redesign fields (NM-44)
    challenge_duration: str | None = Field(
        None, description="e.g. '30_days', '60_days', '90_days'"
    )
    training_types: list[str] | None = Field(
        None, description="e.g. ['strength', 'cardio', 'yoga']"
    )

    # Custom macro overrides (optional, set during onboarding)
    custom_protein_g: float | None = Field(
        None, gt=0, description="Custom protein target in grams"
    )
    custom_carbs_g: float | None = Field(
        None, gt=0, description="Custom carbs target in grams"
    )
    custom_fat_g: float | None = Field(
        None, gt=0, description="Custom fat target in grams"
    )
    weight_loss_kg_per_week: float | None = Field(
        None,
        ge=0.01,
        le=1.0,
        description="Lose-weight pace in kg per week. Omitted keeps the flat cutting deficit.",
    )
    pace_clinician_disclaimer: bool | None = Field(
        None,
        description="User answered yes to the clinician-care question.",
    )

    @model_validator(mode="after")
    def validate_training_and_custom_macros(self):
        from src.domain.services.training_policy import normalize_training_pair

        try:
            self.training_days_per_week, self.training_minutes_per_session = (
                normalize_training_pair(
                    self.training_days_per_week,
                    self.training_minutes_per_session,
                    allow_legacy=True,
                )
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        values = [self.custom_protein_g, self.custom_carbs_g, self.custom_fat_g]
        if sum(value is not None for value in values) not in (0, 3):
            raise ValueError("Custom macros must include protein, carbs, and fat")
        return self
