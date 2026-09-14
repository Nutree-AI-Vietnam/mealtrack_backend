"""
Pydantic schemas for structured meal generation output.
Used with LangChain's with_structured_output() for guaranteed valid responses.
"""

from pydantic import BaseModel, Field


class MealNamesResponse(BaseModel):
    """Phase 1: Response containing 4 diverse meal names."""

    meal_names: list[str] = Field(
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

    meals: list[DiscoveryMealItem] = Field(
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

    ingredients: list[IngredientItem] = Field(
        description="List of 3-8 ingredients with exact amounts",
        min_length=3,
        max_length=8,
    )
    recipe_steps: list[RecipeStepItem] = Field(
        description="List of 2-6 recipe steps with instructions and durations",
        min_length=2,
        max_length=6,
    )
    prep_time_minutes: int = Field(
        description="Total preparation and cooking time in minutes", ge=1, le=120
    )
    # Macros optional — ignored if present; computed deterministically from ingredients
    calories: int | None = Field(
        default=None, description="AI-reported calories (ignored)"
    )
    protein: float | None = Field(
        default=None, description="AI-reported protein (ignored)"
    )
    carbs: float | None = Field(default=None, description="AI-reported carbs (ignored)")
    fat: float | None = Field(default=None, description="AI-reported fat (ignored)")
    # Metadata fields surfaced by the AI for UX enrichment
    origin_country: str | None = Field(
        default=None, description="Country of origin for the dish"
    )
    cuisine_type: str | None = Field(
        default=None, description="Cuisine type (e.g., 'Vietnamese', 'Japanese')"
    )
    emoji: str | None = Field(
        default=None, description="Single emoji representing the dish"
    )


class ChatMealMicros(BaseModel):
    """Estimated portion micronutrients for this meal sitting.
    Omit/null if unknown; do not invent.
    """

    vitamin_a: float | None = Field(default=None, description="Vitamin A in mcg RAE")
    vitamin_c: float | None = Field(default=None, description="Vitamin C in mg")
    vitamin_e: float | None = Field(default=None, description="Vitamin E in mg")
    calcium: float | None = Field(default=None, description="Calcium in mg")
    iron: float | None = Field(default=None, description="Iron in mg")
    magnesium: float | None = Field(default=None, description="Magnesium in mg")
    potassium: float | None = Field(default=None, description="Potassium in mg")
    sodium: float | None = Field(default=None, description="Sodium in mg")
    saturated_fat: float | None = Field(default=None, description="Saturated fat in g")
    added_sugar: float | None = Field(default=None, description="Added sugar in g")


class ChatMealRecipeItem(BaseModel):
    """RecipeDetailsResponse fields plus a name. All properties required for
    OpenAI strict json_schema (no optional/nullable macros).
    """

    name: str = Field(description="Concise meal name (max 60 chars)", max_length=60)
    english_name: str = Field(
        description="English meal name, or repeat name if already English"
    )
    emoji: str = Field(description="Single emoji representing the dish")
    ingredients: list[IngredientItem] = Field(
        description="List of 3-8 ingredients with exact amounts",
        min_length=3,
        max_length=8,
    )
    recipe_steps: list[RecipeStepItem] = Field(
        description="List of 2-6 recipe steps with instructions and durations",
        min_length=2,
        max_length=6,
    )
    prep_time_minutes: int = Field(
        description="Total preparation and cooking time in minutes", ge=1, le=120
    )
    calories: int = Field(
        description="Total estimated portion calories (kcal) for this meal sitting",
        ge=50,
        le=2500,
    )
    protein_g: float = Field(
        description="Estimated protein in grams for this meal sitting", ge=0, le=250
    )
    carbs_g: float = Field(
        description="Estimated carbohydrates in grams for this meal sitting",
        ge=0,
        le=350,
    )
    fat_g: float = Field(
        description="Estimated fat in grams for this meal sitting", ge=0, le=180
    )
    fiber_g: float = Field(
        default=0.0,
        description="Estimated dietary fiber in grams for this meal sitting",
        ge=0,
        le=100,
    )
    micros: ChatMealMicros | None = Field(
        default=None,
        description="Estimated portion micronutrients (vitamins, minerals, limits) for this meal sitting",
    )


class ChatMealRecipeBatch(BaseModel):
    """Next-meal recipe in one structured chat call."""

    meals: list[ChatMealRecipeItem] = Field(
        description="Exactly 1 complete next-meal recipe",
        min_length=1,
        max_length=1,
    )
