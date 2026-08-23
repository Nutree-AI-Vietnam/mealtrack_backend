"""
Meal-related response DTOs.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_serializer


def _serialize_datetime_utc(v: datetime) -> str:
    """Serialize datetime to ISO format with UTC timezone suffix."""
    if v.tzinfo is None:
        v = v.replace(tzinfo=UTC)
    return v.isoformat()


class MealStatusEnum(StrEnum):
    """Enum for meal processing status."""

    pending = "pending"
    analyzing = "analyzing"
    ready = "ready"
    failed = "failed"


class ValueInsightCategoryEnum(StrEnum):
    """Meal value insight category."""

    benefit = "benefit"
    caution = "caution"
    balance = "balance"


# Translation Response DTOs


class ServingUnitResponse(BaseModel):
    """Food-specific serving conversion option."""

    unit: str = Field(..., description="Unit token used for save/edit requests")
    gram_weight: float = Field(..., gt=0, description="Gram weight for one unit")
    description: str = Field(
        "", description="English FatSecret serving description for quantity math"
    )
    display_description: str | None = Field(
        None,
        description="Localized exact serving label; unit stays English",
    )

    @model_serializer
    def serialize_without_empty_label(self) -> dict[str, object]:
        """Keep legacy response shape when no localized label exists."""
        response: dict[str, object] = {
            "unit": self.unit,
            "gram_weight": self.gram_weight,
            "description": self.description,
        }
        if self.display_description is not None:
            response["display_description"] = self.display_description
        return response


class ParsedFoodItem(BaseModel):
    """Response DTO for a single parsed food item."""

    name: str = Field(..., description="Food name")
    quantity: float = Field(..., ge=0, description="Amount")
    unit: str = Field(..., description="Serving unit (localized)")
    calories: float = Field(..., ge=0, description="Calories")
    protein: float = Field(..., ge=0, description="Protein in grams")
    carbs: float = Field(..., ge=0, description="Carbohydrates in grams")
    fat: float = Field(..., ge=0, description="Fat in grams")
    fiber: float = Field(0, ge=0, description="Fiber in grams")
    sugar: float = Field(0, ge=0, description="Sugar in grams")
    data_source: str | None = Field(
        None, description="Data source: usda, fatsecret, or ai_estimate"
    )
    fdc_id: int | None = Field(None, description="USDA FDC ID when available")
    allowed_units: list[ServingUnitResponse] = Field(
        default_factory=list,
        description="Allowed unit options for this food item",
    )
    food_id: str | None = Field(None, description="Namespaced source food identity")
    food_reference_id: int | None = Field(
        None, description="Canonical local food-reference id"
    )
    origin: str | None = Field(None, description="Logical nutrition origin")
    source_namespace: str | None = Field(None, description="Opaque source namespace")
    source_food_id: str | None = Field(None, description="Opaque source food id")
    nutrition_basis: str | None = Field(None, description="Nutrition metric basis")
    nutrition_contract_version: str | None = Field(
        None, description="Backend nutrition contract version"
    )
    calories_per_100g: float | None = Field(
        None, ge=0, description="Backend-derived calories per 100g"
    )
    protein_per_100g: float | None = Field(
        None, ge=0, description="Backend-derived protein per 100g"
    )
    carbs_per_100g: float | None = Field(
        None, ge=0, description="Backend-derived carbs per 100g"
    )
    fat_per_100g: float | None = Field(
        None, ge=0, description="Backend-derived fat per 100g"
    )
    fiber_per_100g: float | None = Field(
        None, ge=0, description="Backend-derived fiber per 100g"
    )
    sugar_per_100g: float | None = Field(
        None, ge=0, description="Backend-derived sugar per 100g"
    )
    canonical_name: str | None = Field(
        None, description="English catalog identity retained beside the display name"
    )
    source_snapshot: dict | None = Field(
        None, description="Validated nutrition snapshot for the confirmed item"
    )


class ParseMealTextResponse(BaseModel):
    """Response DTO for parsed meal text."""

    items: list[ParsedFoodItem] = Field(..., description="Parsed food items")
    total_calories: float = Field(..., description="Total calories")
    total_protein: float = Field(..., description="Total protein in grams")
    total_carbs: float = Field(..., description="Total carbohydrates in grams")
    total_fat: float = Field(..., description="Total fat in grams")
    emoji: str | None = Field(None, description="AI-assigned dish emoji")
    unmatched_terms: list[str] = Field(
        default_factory=list,
        description="Unused. Kept empty for older clients; misses become custom rows.",
    )


class TranslatedFoodItemResponse(BaseModel):
    """Response DTO for translated food item."""

    id: str = Field(..., description="Food item ID")
    name: str = Field(..., description="Translated food item name")
    description: str | None = Field(None, description="Translated description")


class MealTranslationResponse(BaseModel):
    """Response DTO for meal translation."""

    language: str = Field(..., description="ISO 639-1 language code")
    dish_name: str = Field(..., description="Translated dish name")
    meal_instruction: list[dict] | None = Field(
        None, description="Translated instructions: [{instruction, duration_minutes}]"
    )
    meal_ingredients: list[str] | None = Field(
        None, description="Translated ingredient names in food-item order"
    )
    food_items: list[TranslatedFoodItemResponse] = Field(
        default_factory=list, description="Translated food items (legacy)"
    )
    translated_at: datetime | None = Field(
        None, description="When translation was performed"
    )


class MacrosResponse(BaseModel):
    """Response DTO for macronutrient information."""

    protein: float = Field(..., description="Protein in grams")
    carbs: float = Field(..., description="Carbohydrates in grams")
    fat: float = Field(..., description="Fat in grams")
    fiber: float = Field(0, description="Fiber in grams")
    sugar: float = Field(0, description="Sugar in grams")


class NutritionResponse(BaseModel):
    """Response DTO for nutrition information."""

    nutrition_id: str = Field(..., description="Nutrition record ID")
    calories: float = Field(..., description="Calories")
    protein_g: float = Field(..., description="Protein in grams")
    carbs_g: float = Field(..., description="Carbohydrates in grams")
    fat_g: float = Field(..., description="Fat in grams")
    fiber_g: float = Field(0, description="Fiber in grams")
    sugar_g: float = Field(0, description="Sugar in grams")


class NutritionOverrideResponse(BaseModel):
    calories: float
    protein: float
    carbs: float
    fat: float


class CustomNutritionResponse(BaseModel):
    """Response DTO for custom nutrition per 100g."""

    calories_per_100g: float = Field(..., description="Calories per 100g")
    protein_per_100g: float = Field(..., description="Protein per 100g")
    carbs_per_100g: float = Field(..., description="Carbs per 100g")
    fat_per_100g: float = Field(..., description="Fat per 100g")
    fiber_per_100g: float = Field(0, ge=0, description="Fiber per 100g")
    sugar_per_100g: float = Field(0, ge=0, description="Sugar per 100g")


class FoodLabelServingSizeResponse(BaseModel):
    """Serving-size metadata extracted from a Nutrition Facts label."""

    display_text: str = Field(..., description="Serving size text from label")
    grams: float = Field(..., gt=0, description="Serving size in grams")


class FoodLabelMetadataResponse(BaseModel):
    """Client-facing metadata for a food-label scan."""

    product_name: str = Field(..., description="Product name")
    brand: str | None = Field(None, description="Brand when visible")
    serving_size: FoodLabelServingSizeResponse
    servings_per_package: float = Field(..., gt=0)
    label_calories_per_serving: float | None = Field(None, ge=0)
    confidence: float = Field(..., ge=0, le=1)
    label_notes: list[str] = Field(default_factory=list)


class MealValueBulletResponse(BaseModel):
    """Short meal-level value insight."""

    text: str = Field(..., max_length=120)
    category: ValueInsightCategoryEnum
    highlights: list[str] = Field(
        default_factory=list,
        max_length=1,
        description="Exact localized substrings to bold",
    )


class IngredientValueInsightResponse(BaseModel):
    """Short ingredient-level value insight."""

    ingredient_name: str = Field(..., description="Ingredient display name")
    text: str = Field(..., max_length=120)
    category: ValueInsightCategoryEnum
    highlights: list[str] = Field(
        default_factory=list,
        max_length=1,
        description="Exact localized substrings to bold",
    )


class MealValueInsightsResponse(BaseModel):
    """Value insights shown on meal detail."""

    meal_bullets: list[MealValueBulletResponse] = Field(default_factory=list)
    ingredient_insights: list[IngredientValueInsightResponse] = Field(
        default_factory=list
    )


class MealValueInsightsStatusResponse(BaseModel):
    """Cache status for meal value insights."""

    status: str = Field(
        ...,
        description="Insight freshness: fresh, generating, or unavailable",
    )
    value_insights: MealValueInsightsResponse | None = Field(
        None, description="Fresh insights when available"
    )
    version: str | None = Field(
        None, description="Stable fingerprint for the meal insight payload"
    )


class FoodItemResponse(BaseModel):
    """Response DTO for food item information."""

    id: str = Field(..., description="Food item ID")
    name: str = Field(..., description="Food item name")
    display_name: str | None = Field(
        None, description="Localized display name for the requested language"
    )
    canonical_name: str | None = Field(
        None, description="Canonical source food item name"
    )
    name_vi: str | None = Field(
        None, description="Persisted Vietnamese catalog name"
    )
    category: str | None = Field(None, description="Food category")
    quantity: float = Field(..., description="Quantity")
    unit: str = Field(..., description="Unit of measurement")
    description: str | None = Field(None, description="Description")
    nutrition: NutritionResponse | None = Field(
        None, description="Nutrition information"
    )
    custom_nutrition: CustomNutritionResponse | None = Field(
        None, description="Custom nutrition per 100g for custom ingredients"
    )
    source_nutrition: CustomNutritionResponse | None = Field(
        None,
        description="Canonical source nutrition per 100g for source-backed ingredients",
    )
    nutrition_override: NutritionOverrideResponse | None = Field(
        None, description="Independent nutrition values entered by the user"
    )
    fdc_id: int | None = Field(None, description="USDA FDC ID if available")
    food_reference_id: int | None = Field(
        None, description="Canonical food reference ID if available"
    )
    is_custom: bool = Field(False, description="Whether this is a custom ingredient")
    origin: str | None = Field(None, description="Canonical nutrition origin")
    source_namespace: str | None = Field(None, description="Opaque source namespace")
    source_food_id: str | None = Field(None, description="Opaque source food id")
    nutrition_contract_version: str | None = Field(
        None, description="Backend nutrition contract version"
    )
    source_snapshot: dict | None = Field(
        None, description="Immutable nutrition source snapshot used for this save"
    )
    allowed_units: list[ServingUnitResponse] = Field(
        default_factory=list,
        description="Allowed unit options for this food item",
    )


class SimpleMealResponse(BaseModel):
    """Response DTO for basic meal information."""

    meal_id: str = Field(..., description="Meal ID")
    status: MealStatusEnum = Field(..., description="Processing status")
    dish_name: str | None = Field(None, description="Identified dish name")
    emoji: str | None = Field(None, description="AI-assigned food emoji")
    meal_type: str | None = Field(
        None, description="Meal type (breakfast, lunch, dinner, snack)"
    )
    source: str | None = Field(None, description="Meal source")
    ready_at: datetime | None = Field(None, description="When analysis completed")
    error_message: str | None = Field(None, description="Error message if failed")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime | None = Field(None, description="Last update timestamp")

    model_config = ConfigDict(json_encoders={datetime: _serialize_datetime_utc})


class DetailedMealResponse(SimpleMealResponse):
    """Response DTO for detailed meal information with nutrition."""

    food_items: list[FoodItemResponse] = Field(
        default_factory=list, description="Food items in the meal"
    )
    image_url: str | None = Field(None, description="Meal image URL")
    total_calories: float | None = Field(None, description="Total calories")
    total_weight_grams: float | None = Field(None, gt=0, description="Total weight")
    total_nutrition: MacrosResponse | None = Field(None, description="Total macros")
    nutrition_override: NutritionOverrideResponse | None = Field(
        None, description="Independent meal-level nutrition values"
    )
    translations: dict[str, MealTranslationResponse] | None = Field(
        None, description="Translations keyed by language code"
    )
    food_label_metadata: FoodLabelMetadataResponse | None = Field(
        None, description="Nutrition Facts metadata for food-label scans"
    )
    value_insights: MealValueInsightsResponse | None = Field(
        None, description="AI-generated meal and ingredient value insights"
    )
    translation_language: str | None = Field(
        None, description="Language code applied to localized response fields"
    )
    # Recipe details (populated for AI suggestions, null for scanned/manual meals)
    description: str | None = Field(None, description="Meal description")
    instructions: list[dict] | None = Field(
        None, description="Structured recipe steps: [{instruction, duration_minutes}]"
    )
    prep_time_min: int | None = Field(None, description="Prep time in minutes")
    cook_time_min: int | None = Field(None, description="Cook time in minutes")
    cuisine_type: str | None = Field(None, description="Cuisine type")
    origin_country: str | None = Field(None, description="Country of origin")


class MealListResponse(BaseModel):
    """Response DTO for paginated meal list."""

    meals: list[SimpleMealResponse | DetailedMealResponse] = Field(
        ..., description="List of meals"
    )
    total: int = Field(..., ge=0, description="Total number of meals")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(..., ge=1, le=100, description="Items per page")
    total_pages: int = Field(..., ge=0, description="Total pages")


class MealPhotoAnalysisResponse(BaseModel):
    """Response DTO for meal photo analysis."""

    meal_id: str = Field(..., description="ID of the analyzed meal")
    status: MealStatusEnum = Field(..., description="Analysis status")
    message: str = Field(..., description="Status message")
    estimated_completion_seconds: int | None = Field(
        None, description="Estimated seconds until completion"
    )


class MealSearchResponse(BaseModel):
    """Response DTO for meal search results."""

    results: list[SimpleMealResponse] = Field(..., description="Search results")
    query: str = Field(..., description="Original search query")
    total_results: int = Field(..., ge=0, description="Total matching results")


class NutritionSummaryResponse(BaseModel):
    """Response DTO for simplified nutrition summary."""

    meal_name: str = Field(..., description="Identified meal name")
    total_calories: float = Field(..., ge=0, description="Total calories")
    total_weight_grams: float = Field(..., gt=0, description="Total weight in grams")
    calories_per_100g: float = Field(..., ge=0, description="Calories per 100g")
    macros_per_100g: MacrosResponse = Field(..., description="Macronutrients per 100g")
    total_macros: MacrosResponse = Field(..., description="Total macronutrients")
    confidence_score: float = Field(
        ..., ge=0, le=1, description="AI analysis confidence"
    )


class ManualMealCreationResponse(BaseModel):
    """Response DTO for manual meal creation."""

    meal_id: str = Field(..., description="Created meal ID")
    status: str = Field(..., description="Creation status")
    message: str = Field(..., description="Success message")
    created_at: datetime = Field(..., description="Creation timestamp")
    meal_detail: DetailedMealResponse | None = Field(
        None, description="Created meal detail for immediate client-side caching"
    )

    model_config = ConfigDict(json_encoders={datetime: _serialize_datetime_utc})
