"""
Pydantic schemas for structured meal generation output.
Used with LangChain's with_structured_output() for guaranteed valid responses.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class MealNamesResponse(BaseModel):
    """Phase 1: Response containing 4 diverse meal names."""

    meal_names: List[str] = Field(
        description="List of exactly 4 diverse, concise meal names (max 60 chars each) with different cuisines and cooking styles",
        min_length=4,
        max_length=4,
    )

    # B2 FIX: Validate each meal name is reasonably short
    @classmethod
    def __init_subclass__(cls):
        super().__init_subclass__()

    def __init__(self, **data):
        super().__init__(**data)
        # Validate individual meal name lengths
        for i, name in enumerate(self.meal_names):
            if len(name) > 60:
                self.meal_names[i] = name[:57] + "..."  # Truncate if too long


class DiscoveryMealItem(BaseModel):
    """Single meal in lightweight discovery response — name + macros only."""

    name: str = Field(
        description="Concise meal name in English (max 60 chars)", max_length=60
    )
    calories: float = Field(description="Total calories (kcal)", ge=50, le=3000)
    protein: float = Field(description="Protein grams", ge=0, le=300)
    carbs: float = Field(description="Carbohydrate grams", ge=0, le=500)
    fat: float = Field(description="Fat grams", ge=3, le=200)


class DiscoveryMealsResponse(BaseModel):
    """Lightweight discovery: names + macros for 6-10 meals in a single AI call."""

    meals: List[DiscoveryMealItem] = Field(
        description="List of meal suggestions with names and macros",
        min_length=1,
        max_length=12,
    )


class IngredientItem(BaseModel):
    """Single ingredient with amount and unit.

    # Used by: RecipeDetailsResponse (below) — not injected into production pipeline.
    # Recipe details are parsed from raw JSON dicts in recipe_attempt_builder.py.
    """

    name: str = Field(
        description="Ingredient name (e.g., 'chicken breast', 'broccoli')"
    )
    amount: float = Field(description="Quantity amount (e.g., 200, 1.5)", gt=0)
    unit: str = Field(
        description="Unit of measurement (e.g., 'g', 'ml', 'tbsp', 'tsp', 'cup')"
    )


class RecipeStepItem(BaseModel):
    """Single recipe step with instruction and duration."""

    step: int = Field(description="Step number (1, 2, 3, ...)", ge=1)
    instruction: str = Field(description="Clear, actionable instruction")
    duration_minutes: int = Field(description="Time in minutes for this step", ge=0)


class RecipeDetailsResponse(BaseModel):
    """Phase 2: Complete recipe details for a meal (description removed for performance).

    Macros are optional — AI no longer required to calculate them.
    Deterministic macros are calculated from ingredients via NutritionLookupService.
    Injected as structured-output schema via generate_selected_recipes.
    """

    ingredients: List[IngredientItem] = Field(
        description="List of 3-8 ingredients with exact amounts",
        min_length=3,
        max_length=8,
    )
    recipe_steps: List[RecipeStepItem] = Field(
        description="List of 2-6 recipe steps with instructions and durations",
        min_length=2,
        max_length=6,
    )
    prep_time_minutes: int = Field(
        description="Total preparation and cooking time in minutes", ge=5, le=120
    )
    # Macros optional — ignored if present; computed deterministically from ingredients
    calories: Optional[int] = Field(
        default=None, description="AI-reported calories (ignored)"
    )
    protein: Optional[float] = Field(
        default=None, description="AI-reported protein (ignored)"
    )
    carbs: Optional[float] = Field(
        default=None, description="AI-reported carbs (ignored)"
    )
    fat: Optional[float] = Field(default=None, description="AI-reported fat (ignored)")
    # Metadata fields surfaced by the AI for UX enrichment
    origin_country: Optional[str] = Field(
        default=None, description="Country of origin for the dish"
    )
    cuisine_type: Optional[str] = Field(
        default=None, description="Cuisine type (e.g., 'Vietnamese', 'Japanese')"
    )
    emoji: Optional[str] = Field(
        default=None, description="Single emoji representing the dish"
    )


class ChatMealRecipeItem(BaseModel):
    """RecipeDetailsResponse fields plus a name. All properties required for
    OpenAI strict json_schema (no optional/nullable macros).
    """

    name: str = Field(description="Concise meal name (max 60 chars)", max_length=60)
    english_name: str = Field(
        description="English meal name, or repeat name if already English"
    )
    emoji: str = Field(description="Single emoji representing the dish")
    ingredients: List[IngredientItem] = Field(
        description="List of 3-8 ingredients with exact amounts",
        min_length=3,
        max_length=8,
    )
    recipe_steps: List[RecipeStepItem] = Field(
        description="List of 2-6 recipe steps with instructions and durations",
        min_length=2,
        max_length=6,
    )
    prep_time_minutes: int = Field(
        description="Total preparation and cooking time in minutes", ge=5, le=120
    )


class ChatMealRecipeBatch(BaseModel):
    """Three next-meal recipes in one structured chat call."""

    meals: list[ChatMealRecipeItem] = Field(
        description="Exactly 3 complete next-meal recipes",
        min_length=1,
        max_length=3,
    )
