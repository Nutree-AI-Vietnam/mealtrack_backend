"""Request schemas for meal suggestion discovery, recipes, and saving."""

from enum import Enum, StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class MealPortionTypeEnum(StrEnum):
    """Simplified meal portion types."""

    SNACK = "snack"  # Fixed ~150-300 kcal
    MAIN = "main"  # Calculated from TDEE / meals_per_day
    OMAD = "omad"  # DEPRECATED — kept for backward compatibility with older mobile versions


class CookingTimeEnum(int, Enum):
    """Predefined cooking time options."""

    QUICK = 20
    MEDIUM = 30
    STANDARD = 45
    LONG = 60


class DiscoverMealsRequest(BaseModel):
    """Request schema for discovery endpoint — generates 6 meals per batch."""

    meal_type: Literal["breakfast", "lunch", "dinner", "snack"] = Field(
        ..., description="Type of meal"
    )
    meal_portion_type: MealPortionTypeEnum | None = Field(
        None, description="Portion type: snack, main, omad"
    )
    ingredients: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Optional available ingredients (max 20)",
    )
    cooking_time_minutes: CookingTimeEnum | None = Field(
        None,
        description="Cooking time constraint",
    )
    cuisine_region: str | None = Field(
        None,
        description="Preferred cuisine region",
    )
    calorie_target: int | None = Field(
        None,
        gt=0,
        description="Override calorie target",
    )
    protein_target: float | None = Field(None, ge=0)
    carbs_target: float | None = Field(None, ge=0)
    fat_target: float | None = Field(None, ge=0)
    session_id: str | None = Field(
        None,
        description="Session ID for load-more (auto-excludes shown meals)",
    )
    batch_size: int = Field(
        default=10,
        ge=1,
        le=12,
        description="Meals per batch (default 10, max 12)",
    )

    def get_effective_portion_type(self) -> MealPortionTypeEnum:
        if self.meal_portion_type is not None:
            return self.meal_portion_type
        if self.meal_type == "snack":
            return MealPortionTypeEnum.SNACK
        return MealPortionTypeEnum.MAIN


class GenerateRecipesRequest(BaseModel):
    """Request to generate full recipes for 1-3 selected discovery meals."""

    meal_names: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="English meal names from discovery grid (1-3)",
    )
    session_id: str | None = Field(
        None, description="Discovery session id returned by /discover"
    )
    selected_meal_ids: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="Stable discovery meal ids selected by the client",
    )
    selected_meals: list[dict] = Field(
        default_factory=list,
        max_length=3,
        description="Full selected discovery meal objects from /discover",
    )
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"] = Field(
        ..., description="Type of meal"
    )
    calorie_target: int | None = Field(None, gt=0)
    cuisine_region: str | None = None
    ingredients: list[str] = Field(default_factory=list, max_length=20)
    cooking_time_minutes: int | None = Field(None, ge=5, le=120)
    protein_target: float | None = Field(None, ge=0)
    carbs_target: float | None = Field(None, ge=0)
    fat_target: float | None = Field(None, ge=0)

    @model_validator(mode="after")
    def validate_selection(self):
        if (
            not self.selected_meal_ids
            and not self.selected_meals
            and not self.meal_names
        ):
            raise ValueError("Provide selected_meal_ids, selected_meals, or meal_names")
        return self


class SaveInstructionItem(BaseModel):
    """A single cooking instruction step with optional duration."""

    instruction: str = Field(..., description="Instruction text")
    duration_minutes: int | None = Field(
        None, ge=0, description="Estimated duration in minutes"
    )


class SaveIngredientItem(BaseModel):
    """
    A single ingredient with quantity, unit, and optional per-item macros.

    Per-ingredient macros default to 0 when the caller does not have them (e.g.
    when saving directly from an AI suggestion). They should be populated when
    available (e.g. from a local nutrition DB lookup) so that later ingredient
    edits can recalculate meal-level totals correctly.
    """

    name: str = Field(..., description="Ingredient name")
    amount: float = Field(..., gt=0, description="Quantity/amount")
    unit: str = Field(..., description="Unit (g, ml, tbsp, tsp, etc.)")
    calories: float = Field(
        default=0.0, ge=0, description="Calories for this ingredient (0 if unknown)"
    )
    food_reference_id: int | None = Field(
        None,
        gt=0,
        description="Canonical food reference ID when the ingredient was resolved",
    )
    protein: float = Field(
        default=0.0, ge=0, description="Protein in grams (0 if unknown)"
    )
    carbs: float = Field(
        default=0.0, ge=0, description="Carbohydrates in grams (0 if unknown)"
    )
    fat: float = Field(default=0.0, ge=0, description="Fat in grams (0 if unknown)")
    fiber: float = Field(default=0.0, ge=0, description="Fiber in grams (0 if unknown)")


class SaveMealSuggestionRequest(BaseModel):
    """
    Request schema for saving a meal suggestion as a regular meal.
    """

    suggestion_id: str = Field(..., description="ID of the suggestion being saved")
    name: str = Field(..., description="Name of the meal")
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"] = Field(
        ..., description="Type of meal"
    )
    calories: int | None = Field(
        None,
        gt=0,
        description="Total calories. If omitted, derived from macros (protein*4 + carbs*4 + fat*9).",
    )
    protein: float = Field(..., ge=0, description="Protein in grams (already scaled)")
    carbs: float = Field(
        ..., ge=0, description="Carbohydrates in grams (already scaled)"
    )
    fat: float = Field(..., ge=0, description="Fat in grams (already scaled)")
    fiber: float = Field(
        default=0.0, ge=0, description="Fiber in grams (already scaled)"
    )
    description: str | None = Field(None, description="Meal description")
    estimated_cook_time_minutes: int | None = Field(
        None, ge=0, description="Estimated cooking time in minutes"
    )
    ingredients: list[SaveIngredientItem] = Field(
        default_factory=list,
        description="Structured ingredient list with name, amount, and unit",
    )
    instructions: list[str | SaveInstructionItem] = Field(
        default_factory=list,
        description="Cooking instructions as strings or {instruction, duration_minutes} objects",
    )
    portion_multiplier: int = Field(
        default=1,
        ge=1,
        description="Number of servings (informational only, values already scaled by AI)",
    )
    meal_date: str = Field(
        ..., description="Target date for the meal (YYYY-MM-DD format)"
    )
    cuisine_type: str | None = Field(
        None, description="Cuisine type (e.g., Asian, Vietnamese)"
    )
    origin_country: str | None = Field(None, description="Country of origin")
    emoji: str | None = Field(None, description="AI-assigned food emoji")
    image_url: str | None = Field(
        None, description="Food image URL from discovery (Pexels/Unsplash hotlink)"
    )
    unsplash_download_location: str | None = Field(
        None,
        description="Unsplash download_location URL — triggers download event per API guidelines",
    )
    meal_id: str | None = Field(
        None,
        max_length=36,
        description="Client-assigned meal UUID to persist and reopen after save",
    )

    @field_validator("meal_id")
    @classmethod
    def validate_meal_id_uuid(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        try:
            return str(UUID(str(value).strip()))
        except ValueError as exc:
            raise ValueError("meal_id must be a valid UUID") from exc

    @field_validator("meal_date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        """Validate date format is YYYY-MM-DD."""
        from datetime import datetime

        try:
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except ValueError as e:
            raise ValueError("meal_date must be in YYYY-MM-DD format") from e

    class Config:
        json_schema_extra = {
            "example": {
                "suggestion_id": "suggestion_123",
                "name": "Grilled Chicken Salad",
                "meal_type": "lunch",
                "calories": 450,
                "protein": 35.0,
                "carbs": 25.0,
                "fat": 20.0,
                "description": "Healthy grilled chicken salad",
                "estimated_cook_time_minutes": 30,
                "ingredients_list": ["chicken breast", "lettuce", "tomatoes"],
                "instructions": ["Grill chicken", "Chop vegetables", "Mix together"],
                "portion_multiplier": 1,
                "meal_date": "2024-01-15",
            }
        }
