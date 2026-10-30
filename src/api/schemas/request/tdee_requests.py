"""
TDEE calculation request DTOs.
"""

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class SexEnum(StrEnum):
    """Enum for biological sex."""

    male = "male"
    female = "female"


class JobTypeEnum(StrEnum):
    """Enum for job types based on daily movement requirements."""

    desk = "desk"
    on_feet = "on_feet"
    physical = "physical"


class GoalEnum(StrEnum):
    """Enum for fitness goals."""

    cut = "cut"
    bulk = "bulk"
    recomp = "recomp"


class TrainingLevelEnum(StrEnum):
    """Enum for training experience levels."""

    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"


class DietTypeEnum(StrEnum):
    classic = "classic"
    keto = "keto"
    vegetarian = "vegetarian"
    vegan = "vegan"


class UnitSystemEnum(StrEnum):
    """Enum for unit systems."""

    metric = "metric"
    imperial = "imperial"


class TdeeCalculationRequest(BaseModel):
    """Request DTO for TDEE calculation matching Flutter OnboardingData."""

    age: int = Field(..., ge=12, le=120, description="User age")
    sex: SexEnum = Field(..., description="User biological sex")
    height: float = Field(..., gt=0, description="Height in user's preferred units")
    weight: float = Field(..., gt=0, description="Weight in user's preferred units")
    body_fat_percentage: float | None = Field(
        None, ge=5, le=55, description="Body fat percentage (optional)"
    )
    job_type: JobTypeEnum = Field(..., description="Job type (desk, on_feet, physical)")
    training_days_per_week: int = Field(
        ..., ge=0, le=7, description="Days of training per week"
    )
    training_minutes_per_session: int = Field(
        ..., ge=0, le=180, description="Minutes per training session"
    )
    goal: GoalEnum = Field(..., description="Fitness goal")
    unit_system: UnitSystemEnum = Field(
        UnitSystemEnum.metric, description="Unit system for height/weight"
    )
    training_level: TrainingLevelEnum | None = Field(
        None, description="Training experience level (beginner, intermediate, advanced)"
    )
    diet_type: DietTypeEnum = Field(
        DietTypeEnum.classic, description="Stable dietary policy key"
    )
    custom_protein_g: float | None = Field(None, gt=0)
    custom_carbs_g: float | None = Field(None, gt=0)
    custom_fat_g: float | None = Field(None, gt=0)
    requested_calories: float | None = Field(None, gt=0, le=10000)
    weight_loss_kg_per_week: float | None = Field(
        None,
        gt=0,
        le=1.0,
        description="Lose-weight pace in kg per week. Omitted keeps the flat cutting deficit.",
    )

    @model_validator(mode="after")
    def validate_measurements_with_units(self):
        """Validate height and weight based on unit system."""
        unit_system = self.unit_system
        height = self.height
        weight = self.weight

        if height is not None and unit_system is not None:
            if unit_system == UnitSystemEnum.metric:
                if not (100 <= height <= 272):
                    raise ValueError(
                        "Height must be between 100-272 cm for metric system"
                    )
            else:  # imperial
                if not (39 <= height <= 107):
                    raise ValueError(
                        "Height must be between 39-107 inches for imperial system"
                    )

        if weight is not None and unit_system is not None:
            if unit_system == UnitSystemEnum.metric:
                if not (30 <= weight <= 250):
                    raise ValueError(
                        "Weight must be between 30-250 kg for metric system"
                    )
            else:  # imperial
                if not (66 <= weight <= 551):
                    raise ValueError(
                        "Weight must be between 66-551 lbs for imperial system"
                    )

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

        custom_values = [self.custom_protein_g, self.custom_carbs_g, self.custom_fat_g]
        custom_count = sum(value is not None for value in custom_values)
        if custom_count not in (0, 3):
            raise ValueError("Custom macros must include protein, carbs, and fat")
        if custom_count and self.requested_calories is not None:
            raise ValueError("requested_calories cannot be combined with custom macros")
        return self

    class Config:
        json_schema_extra = {
            "example": {
                "age": 25,
                "sex": "male",
                "height": 180.0,
                "weight": 75.0,
                "body_fat_percentage": 15.0,
                "job_type": "desk",
                "training_days_per_week": 4,
                "training_minutes_per_session": 60,
                "goal": "recomp",
                "unit_system": "metric",
                "diet_type": "classic",
            }
        }


class BatchTdeeCalculationRequest(BaseModel):
    """Request DTO for batch TDEE calculations."""

    calculations: list[TdeeCalculationRequest] = Field(
        ...,
        min_items=1,
        max_items=10,
        description="List of TDEE calculations to perform",
    )
