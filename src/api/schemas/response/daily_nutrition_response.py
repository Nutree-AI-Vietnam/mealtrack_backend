"""
Daily nutrition summary response DTO.
"""


from pydantic import BaseModel, Field


class MacrosResponse(BaseModel):
    """Macronutrient response model."""

    protein: float = Field(..., description="Protein in grams")
    carbs: float = Field(..., description="Carbohydrates in grams")
    fat: float = Field(..., description="Fat in grams")
    fiber: float = Field(0, ge=0, description="Fiber in grams")
    sugar: float = Field(0, ge=0, description="Sugar in grams")


class HydrationSummaryResponse(BaseModel):
    """Hydration summary for daily nutrition response."""

    consumed_ml: int = Field(..., description="Water consumed today in ml")
    goal_ml: int = Field(..., description="Daily water goal in ml")
    percentage: float = Field(..., description="Percentage of daily goal achieved")


class WeeklyContextResponse(BaseModel):
    """Weekly budget context for daily response."""

    adjusted_target_calories: float = Field(
        ..., description="Adjusted daily calories based on weekly budget"
    )
    adjusted_target_carbs: float = Field(
        ..., description="Adjusted daily carbs based on weekly budget"
    )
    adjusted_target_fat: float = Field(
        ..., description="Adjusted daily fat based on weekly budget"
    )
    daily_protein: float = Field(..., description="Daily protein target (fixed)")
    bmr_floor_active: bool = Field(
        ..., description="True if adjusted target hit BMR floor"
    )
    remaining_days: int = Field(..., description="Days remaining in the week")


class MicronutrientTargetsResponse(BaseModel):
    """Individual DRI daily goals for Home / Progress beakers."""

    iron_mg: float
    fiber_g: float
    potassium_mg: float
    sodium_mg: float
    added_sugar_g: float


class DailyNutritionResponse(BaseModel):
    """Response DTO for daily nutrition summary - matches Flutter frontend expectations."""

    date: str = Field(..., description="Date in YYYY-MM-DD format")
    target_calories: float = Field(..., description="Target calories for the day")
    target_macros: MacrosResponse = Field(..., description="Target macros for the day")
    consumed_calories: float = Field(
        ...,
        description="NET calories consumed (food intake minus movement burn). "
        "Clients must NOT subtract burn again from this value.",
    )
    food_calories: float | None = Field(
        None,
        description="GROSS food calories consumed, before subtracting movement "
        "burn. Clients that apply burn themselves should use this to avoid "
        "double-subtraction.",
    )
    movement_kcal_burned: float = Field(
        0.0, ge=0, description="Active calories burned from movement in the balance"
    )
    consumed_macros: MacrosResponse = Field(..., description="Macros consumed so far")
    remaining_calories: float = Field(..., description="Remaining calories for the day")
    remaining_macros: MacrosResponse = Field(
        ..., description="Remaining macros for the day"
    )
    completion_percentage: dict[str, float] = Field(
        ..., description="Completion percentage for calories and macros"
    )
    weekly_context: WeeklyContextResponse | None = Field(
        None, description="Weekly budget context"
    )
    hydration: HydrationSummaryResponse | None = Field(
        None, description="Daily hydration summary"
    )
    iron_mg: float | None = Field(None, description="Logged iron; null if not logged")
    potassium_mg: float | None = Field(
        None, description="Logged potassium; null if not logged"
    )
    sodium_mg: float | None = Field(
        None, description="Logged sodium; null if not logged"
    )
    added_sugar_g: float | None = Field(
        None, description="Logged added sugar; null if not logged"
    )
    micro_targets: MicronutrientTargetsResponse | None = Field(
        None, description="DRI daily goals for the five Home highlight nutrients"
    )
