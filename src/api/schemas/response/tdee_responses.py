"""
TDEE calculation response DTOs.
"""

from pydantic import BaseModel, Field

from src.api.schemas.request.tdee_requests import GoalEnum

TDEE_PREVIEW_CALCULATION_CONTRACT = "onboarding_preview_v2"


class PaceRangeResponse(BaseModel):
    """Lose-weight paces this person can pick, in kg per week (cut goal only)."""

    min_kg_per_week: float = Field(..., description="Slow limit of the slider")
    max_kg_per_week: float = Field(
        ...,
        description="Fastest pace before the target drops below the calorie floor; "
        "can be under min_kg_per_week, and 0 when no deficit fits",
    )
    recommended_low_kg: float = Field(..., description="0.5% of body weight per week")
    recommended_high_kg: float = Field(..., description="1% of body weight per week")

    @classmethod
    def from_result(cls, result: dict) -> "PaceRangeResponse | None":
        pace_range = result.get("pace_range")
        return cls(**pace_range) if pace_range else None


class MacroTargetsResponse(BaseModel):
    """Response DTO for macro targets matching Flutter MacroTargets."""

    calories: float = Field(..., ge=0, description="Daily calorie target")
    protein: float = Field(..., ge=0, description="Protein in grams per day")
    fat: float = Field(..., ge=0, description="Fat in grams per day")
    carbs: float = Field(..., ge=0, description="Carbohydrates in grams per day")

    class Config:
        json_schema_extra = {
            "example": {
                "calories": 2500.0,
                "protein": 125.0,
                "fat": 83.3,
                "carbs": 300.0,
            }
        }


class TdeeCalculationResponse(BaseModel):
    """Response DTO for TDEE calculation matching Flutter TdeeResult."""

    bmr: float = Field(..., gt=0, description="Basal Metabolic Rate")
    tdee: float = Field(..., gt=0, description="Total Daily Energy Expenditure")
    macros: MacroTargetsResponse = Field(..., description="Macro targets for the goal")
    goal: GoalEnum = Field(..., description="Goal used for calculation")

    calculation_contract: str | None = Field(
        None,
        description="Versioned calculation policy used to produce this response",
    )

    # Additional useful information
    activity_multiplier: float | None = Field(
        None, description="Activity level multiplier used"
    )
    formula_used: str | None = Field(
        None, description="Formula used (Mifflin-St Jeor or Katch-McArdle)"
    )
    is_custom: bool = Field(
        False,
        description="True when macros are user-customized (not algorithm-calculated)",
    )
    macro_preset: str | None = Field(
        None, description="Macro preset used for cache validation"
    )
    target_revision: int | None = Field(
        None, description="Target revision for cache validation"
    )
    profile_target_revision: int | None = Field(
        None, description="Profile target revision for cache validation"
    )
    pace_range: PaceRangeResponse | None = Field(
        None, description="Lose-weight pace range; present for a cut goal"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "bmr": 1750.0,
                "tdee": 2450.0,
                "macros": {
                    "calories": 2450.0,
                    "protein": 122.5,
                    "fat": 81.7,
                    "carbs": 294.0,
                },
                "goal": "recomp",
                "activity_multiplier": 1.4,
                "formula_used": "Mifflin-St Jeor",
                "is_custom": False,
                "macro_preset": "standard",
                "calculation_contract": TDEE_PREVIEW_CALCULATION_CONTRACT,
                "target_revision": 0,
            }
        }


class BatchTdeeCalculationResponse(BaseModel):
    """Response DTO for batch TDEE calculations."""

    results: list[TdeeCalculationResponse] = Field(
        ..., description="List of TDEE calculation results"
    )
    total_calculations: int = Field(
        ..., ge=0, description="Total calculations performed"
    )


class TdeeComparisonResponse(BaseModel):
    """Response DTO for comparing TDEE calculations."""

    current: TdeeCalculationResponse = Field(
        ..., description="Current TDEE calculation"
    )
    previous: TdeeCalculationResponse | None = Field(
        None, description="Previous TDEE calculation for comparison"
    )
    changes: dict | None = Field(None, description="Changes between calculations")


class TdeeHistoryResponse(BaseModel):
    """Response DTO for TDEE calculation history."""

    user_id: str = Field(..., description="User ID")
    calculations: list[dict] = Field(..., description="List of historical calculations")
    total_count: int = Field(..., ge=0, description="Total number of calculations")


class TdeeErrorResponse(BaseModel):
    """Response DTO for TDEE calculation errors."""

    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Error message")
    field: str | None = Field(None, description="Field that caused the error")
