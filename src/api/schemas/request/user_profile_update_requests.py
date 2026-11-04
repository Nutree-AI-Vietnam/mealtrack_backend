from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from src.domain.model.user.core_user import age_on


class GoalEnum(StrEnum):
    cut = "cut"
    bulk = "bulk"
    recomp = "recomp"


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


class UpdateFitnessGoalRequest(BaseModel):
    goal: GoalEnum = Field(..., description="New fitness goal")


class UpdateMetricsRequest(BaseModel):
    """Unified update for weight, job type, training, body fat, goal, target weight and diet."""

    model_config = ConfigDict(extra="forbid")

    age: int | None = Field(None, ge=12, le=120, description="User age")
    date_of_birth: date | None = Field(
        None,
        description=(
            "Date of birth as YYYY-MM-DD. Sets age as of today and wins over an "
            "age sent with it."
        ),
    )
    height_cm: float | None = Field(None, ge=100, le=272, description="Height in cm")
    weight_kg: float | None = Field(None, description="Weight in kg", gt=0)
    biological_sex: str | None = Field(
        None, description="Biological sex (male, female)"
    )
    job_type: str | None = Field(None, description="Job type (desk, on_feet, physical)")
    weekly_weight_change_kg: float | None = Field(
        None, description="Planned weekly weight change in kg"
    )
    training_days_per_week: int | None = Field(
        None, ge=0, le=7, description="Training days per week"
    )
    training_minutes_per_session: int | None = Field(
        None, ge=0, le=180, description="Minutes per training session"
    )
    body_fat_percentage: float | None = Field(
        None,
        validation_alias=AliasChoices("body_fat_percentage", "body_fat_percent"),
        description="Body fat percentage",
        ge=0,
        le=70,
    )
    reset_body_fat: bool = Field(
        False, description="Clear saved body fat percentage when true"
    )
    fitness_goal: GoalEnum | None = Field(
        None, description="Fitness goal (cut, bulk, recomp)"
    )
    training_level: TrainingLevelEnum | None = Field(
        None, description="Training level (beginner, intermediate, advanced)"
    )
    target_weight_kg: float | None = Field(
        None, description="Target weight in kg", gt=0
    )
    goal_start_weight_kg: float | None = Field(
        None, description="Weight when goal journey started", gt=0
    )
    goal_started_at: datetime | None = Field(
        None, description="Timestamp when goal journey started"
    )
    daily_water_goal_ml: int | None = Field(
        None,
        gt=0,
        description="Custom daily water goal in ml (null = use weight-based formula)",
    )
    reset_water_goal: bool = Field(
        False,
        description="Reset daily water goal to weight-based calculation (35 ml/kg)",
    )
    weight_loss_kg_per_week: float | None = Field(
        None,
        ge=0.01,
        le=1.0,
        description="Lose-weight pace in kg per week. Omitted keeps the current pace.",
    )
    dietary_preferences: list[Annotated[str, Field(max_length=40)]] | None = Field(
        None,
        max_length=10,
        description="Diets such as vegetarian or keto. Omitted keeps the saved list.",
    )

    @field_validator("date_of_birth")
    @classmethod
    def validate_date_of_birth(cls, value: date | None) -> date | None:
        if value is None:
            return None
        today = date.today()
        if value > today:
            raise ValueError("Date of birth cannot be in the future")
        if not 12 <= age_on(value, today) <= 120:
            raise ValueError("Date of birth must give an age between 12 and 120")
        return value

    @model_validator(mode="after")
    def validate_pair_when_complete(self):
        # The handler merges a partial request with persisted values. Validate the
        # complete pair here only when both fields were supplied.
        if (
            self.training_days_per_week is not None
            and self.training_minutes_per_session is not None
        ):
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
        return self
