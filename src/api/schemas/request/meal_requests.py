"""
Meal-related request DTOs.
"""

import warnings
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from src.domain.constants.languages import SUPPORTED_TRANSLATION_LANGUAGES
from src.domain.model.nutrition.macros import Macros
from src.domain.services.prompts.input_sanitizer import validate_refinement_items


class ParseMealTextRequest(BaseModel):
    """Request DTO for parsing meal text descriptions."""

    text: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Natural language food description",
    )
    current_items: Optional[list[dict[str, Any]]] = Field(
        None,
        description="Current meal items for refinement (when user is editing an existing meal)",
    )

    @model_validator(mode="after")
    def validate_refinement_context(self) -> "ParseMealTextRequest":
        self.current_items = validate_refinement_items(self.current_items)
        return self


class MacrosRequest(BaseModel):
    """Request DTO for macronutrient information."""

    protein: float = Field(..., ge=0, description="Protein in grams")
    carbs: float = Field(..., ge=0, description="Carbohydrates in grams")
    fat: float = Field(..., ge=0, description="Fat in grams")


class CreateMealRequest(BaseModel):
    """Request DTO for creating a meal manually."""

    name: str = Field(..., min_length=1, max_length=200, description="Meal name")
    description: Optional[str] = Field(
        None, max_length=500, description="Meal description"
    )
    weight_grams: Optional[float] = Field(
        None, gt=0, le=5000, description="Weight in grams"
    )
    calories_per_100g: Optional[float] = Field(
        None, ge=0, description="Calories per 100g"
    )
    macros_per_100g: Optional[MacrosRequest] = Field(
        None, description="Macros per 100g"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Grilled Chicken Breast",
                "description": "Seasoned with herbs and olive oil",
                "weight_grams": 150,
                "calories_per_100g": 165,
                "macros_per_100g": {"protein": 31.0, "carbs": 0, "fat": 3.6},
            }
        }


class UpdateMealRequest(BaseModel):
    """Request DTO for updating meal information."""

    name: Optional[str] = Field(
        None, min_length=1, max_length=200, description="Meal name"
    )
    description: Optional[str] = Field(
        None, max_length=500, description="Meal description"
    )
    weight_grams: Optional[float] = Field(
        None, gt=0, le=5000, description="Weight in grams"
    )
    calories_per_100g: Optional[float] = Field(
        None, ge=0, description="Calories per 100g"
    )
    macros_per_100g: Optional[MacrosRequest] = Field(
        None, description="Macros per 100g"
    )


class UpdateMealMacrosRequest(BaseModel):
    """Request DTO for updating meal portion size."""

    weight_grams: float = Field(
        ..., gt=0, le=5000, description="Weight of the meal portion in grams"
    )

    class Config:
        json_schema_extra = {"example": {"weight_grams": 250.0}}


class MealSearchRequest(BaseModel):
    """Request DTO for searching meals."""

    query: str = Field(..., min_length=1, max_length=200, description="Search query")
    limit: int = Field(10, ge=1, le=100, description="Maximum results to return")
    include_ingredients: bool = Field(
        False, description="Include ingredients in search"
    )

    class Config:
        json_schema_extra = {
            "example": {"query": "chicken", "limit": 20, "include_ingredients": True}
        }


class AnalyzeMealImageRequest(BaseModel):
    """Request DTO for meal image analysis options."""

    immediate_analysis: bool = Field(
        False, description="Perform immediate analysis (synchronous)"
    )
    portion_size_grams: Optional[float] = Field(
        None, gt=0, le=5000, description="Known portion size in grams"
    )
    context: Optional[str] = Field(
        None, max_length=500, description="Additional context for analysis"
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code for translated results (en, vi, es, fr, de, ja, zh)",
    )

    @field_validator("language")
    @classmethod
    def validate_language_code(cls, v: str) -> str:
        """Validate language code and fallback to 'en' if invalid."""
        valid_languages = SUPPORTED_TRANSLATION_LANGUAGES
        normalized = v.lower().strip()
        if normalized not in valid_languages:
            warnings.warn(
                f"Unsupported language code '{v}', falling back to 'en'",
                UserWarning,
                stacklevel=2,
            )
            return "en"
        return normalized


def _has_macro_over_100(nutrition) -> bool:
    return any(
        getattr(nutrition, field, 0) > 100
        for field in (
            "protein_per_100g",
            "carbs_per_100g",
            "fat_per_100g",
            "fiber_per_100g",
            "sugar_per_100g",
        )
    )


def _is_gram_unit(unit: str) -> bool:
    return unit.lower().strip() in {"g", "gram", "grams"}


# Food database manual meal creation requests
class ManualMealCustomNutritionRequest(BaseModel):
    """Custom nutrition for manual meal creation.

    Prompt-created meals from older mobile clients can send high per-100g values
    after back-calculating absolute parsed macros from a non-gram display unit.
    The manual meal handler converts them back through the same unit path.
    """

    protein_per_100g: float = Field(..., ge=0, description="Protein per 100g")
    carbs_per_100g: float = Field(..., ge=0, description="Carbohydrates per 100g")
    fat_per_100g: float = Field(..., ge=0, description="Fat per 100g")
    fiber_per_100g: float = Field(0.0, ge=0, description="Fiber per 100g")
    sugar_per_100g: float = Field(0.0, ge=0, description="Sugar per 100g")

    @property
    def calories_per_100g(self) -> float:
        """Derive calories from macros using fiber-aware formula."""
        return round(
            Macros.raw_total_calories(
                self.protein_per_100g,
                self.carbs_per_100g,
                self.fat_per_100g,
                self.fiber_per_100g,
            ),
            2,
        )


class ManualMealItemRequest(BaseModel):
    """Single selected food item with portion to create a manual meal.

    Supports both USDA foods (via fdc_id) and custom foods (via name + custom_nutrition).
    """

    fdc_id: Optional[int] = Field(
        None, description="USDA FDC ID (required for USDA foods)"
    )
    origin: Optional[Literal["local", "usda", "provider", "custom"]] = Field(
        None, description="Canonical nutrition origin for v2 saves"
    )
    food_reference_id: Optional[int] = Field(
        None, description="Canonical local food-reference ID"
    )
    source_namespace: Optional[str] = Field(
        None, min_length=1, max_length=64, description="Provider namespace"
    )
    source_food_id: Optional[str] = Field(
        None, min_length=1, max_length=255, description="Opaque provider food ID"
    )
    food_id: Optional[str] = Field(
        None, min_length=1, max_length=255, description="Deprecated response alias"
    )
    name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=200,
        description="Food name (required for custom foods)",
    )
    quantity: float = Field(
        ..., gt=0, description="Amount relative to serving unit (e.g., grams)"
    )
    unit: str = Field(
        "g", min_length=1, max_length=120, description="Unit, default grams"
    )
    serving_options: Optional[list[dict[str, Any]]] = Field(
        None,
        description="Validated serving measurements for custom foods",
    )
    source_snapshot: Optional[dict[str, Any]] = Field(
        None,
        description="Validated nutrition snapshot returned by parse/search flows",
    )
    custom_nutrition: Optional[ManualMealCustomNutritionRequest] = Field(
        None,
        description="Custom nutrition data for non-USDA foods",
    )


class CreateManualMealFromFoodsRequest(BaseModel):
    """Create a manual meal from selected USDA foods with portions."""

    dish_name: str = Field(..., min_length=1, max_length=200)
    items: list[ManualMealItemRequest] = Field(..., min_items=1, max_length=50)
    nutrition_contract_version: Optional[int] = Field(
        None, description="Versioned authoritative nutrition save contract"
    )
    meal_type: Optional[str] = Field(
        None, description="Meal type: breakfast, lunch, dinner, or snack"
    )
    target_date: Optional[str] = Field(
        None, description="Target date in YYYY-MM-DD format for meal association"
    )
    source: Optional[str] = Field(
        None, description="Meal source: scanner, prompt, food_search, manual"
    )
    emoji: Optional[str] = Field(None, description="AI-assigned dish emoji")

    @model_validator(mode="after")
    def validate_custom_nutrition_bounds(self):
        source = (self.source or "").lower()
        prompt_sources = {"prompt", "parse_text", "ai_prompt"}

        for item in self.items:
            if not item.custom_nutrition or not _has_macro_over_100(
                item.custom_nutrition
            ):
                continue

            is_legacy_prompt_payload = source in prompt_sources or not _is_gram_unit(
                item.unit or ""
            )
            if not is_legacy_prompt_payload:
                raise ValueError(
                    "custom_nutrition macros must be <=100g per 100g "
                    "unless payload uses a legacy prompt unit"
                )
        self._validate_origin_contract()
        return self

    def _validate_origin_contract(self) -> None:
        has_v2_fields = any(
            item.origin is not None
            or item.food_reference_id is not None
            or item.source_food_id is not None
            or item.source_namespace is not None
            or item.food_id is not None
            for item in self.items
        )
        if self.nutrition_contract_version not in (None, 2):
            raise ValueError("unsupported nutrition_contract_version")
        if self.nutrition_contract_version != 2:
            if has_v2_fields:
                raise ValueError("origin requires nutrition_contract_version=2")
            return

        for item in self.items:
            if item.origin is None:
                raise ValueError("v2 items require origin")
            if item.food_id is not None:
                raise ValueError("v2 saves must use dedicated source identifiers")
            if item.origin == "local":
                if item.food_reference_id is None or any(
                    value is not None
                    for value in (
                        item.fdc_id,
                        item.source_namespace,
                        item.source_food_id,
                    )
                ):
                    raise ValueError("local origin requires only food_reference_id")
                if item.custom_nutrition is not None and item.source_snapshot is None:
                    raise ValueError("prepared local items require source_snapshot")
            elif item.origin == "usda":
                if item.fdc_id is None or any(
                    value is not None
                    for value in (
                        item.food_reference_id,
                        item.source_namespace,
                        item.source_food_id,
                    )
                ):
                    raise ValueError("usda origin requires only fdc_id")
                if item.custom_nutrition is not None and item.source_snapshot is None:
                    raise ValueError("prepared usda items require source_snapshot")
            elif item.origin == "provider":
                if item.source_food_id is None or any(
                    value is not None
                    for value in (
                        item.fdc_id,
                        item.food_reference_id,
                    )
                ):
                    raise ValueError("provider origin requires source_food_id")
                if item.custom_nutrition is not None and item.source_snapshot is None:
                    raise ValueError("prepared provider items require source_snapshot")
            elif item.origin == "custom":
                if item.custom_nutrition is None or any(
                    value is not None
                    for value in (
                        item.fdc_id,
                        item.food_reference_id,
                        item.source_namespace,
                        item.source_food_id,
                    )
                ):
                    raise ValueError("custom origin requires only custom_nutrition")


# Meal Edit Feature Requests
class FoodItemChangeRequest(BaseModel):
    """Request DTO for a single food item change in meal editing."""

    action: Literal["add", "update", "remove"] = Field(
        ..., description="Action to perform: 'add', 'update', or 'remove'"
    )
    id: Optional[str] = Field(
        None, description="ID of existing food item (required for update/remove)"
    )
    fdc_id: Optional[int] = Field(None, description="USDA FDC ID for new ingredients")
    origin: Optional[Literal["local", "usda", "provider", "custom"]] = Field(
        None, description="Canonical nutrition origin for v2 actions"
    )
    food_reference_id: Optional[int] = Field(None)
    source_namespace: Optional[str] = Field(None, min_length=1, max_length=64)
    source_food_id: Optional[str] = Field(None, min_length=1, max_length=255)
    food_id: Optional[str] = Field(None, min_length=1, max_length=255)
    name: Optional[str] = Field(
        None, min_length=1, max_length=200, description="Ingredient name"
    )
    quantity: Optional[float] = Field(
        None, gt=0, le=10000, description="Quantity amount"
    )
    unit: Optional[str] = Field(
        None, min_length=1, max_length=120, description="Unit of measurement"
    )
    custom_nutrition: Optional["CustomNutritionRequest"] = Field(
        None, description="Custom nutrition data for non-USDA ingredients"
    )
    nutrition_override: Optional["NutritionOverrideRequest"] = Field(
        None, description="Independent nutrition values entered by the user"
    )
    clear_nutrition_override: bool = Field(
        False, description="Restore source nutrition for this ingredient"
    )
    override_intent: Optional[Literal["user_entered"]] = Field(
        None, description="Explicit user intent for an absolute nutrition override"
    )

    class Config:
        json_schema_extra = {
            "example": {"action": "update", "id": "123", "quantity": 150.0, "unit": "g"}
        }


class CustomNutritionRequest(BaseModel):
    """Request DTO for custom nutrition data.

    Calories always derived from macros using the canonical fiber-aware formula.
    """

    protein_per_100g: float = Field(
        ..., ge=0, allow_inf_nan=False, description="Protein per 100g in grams"
    )
    carbs_per_100g: float = Field(
        ..., ge=0, allow_inf_nan=False, description="Carbohydrates per 100g in grams"
    )
    fat_per_100g: float = Field(
        ..., ge=0, allow_inf_nan=False, description="Fat per 100g in grams"
    )
    fiber_per_100g: float = Field(
        0.0, ge=0, allow_inf_nan=False, description="Fiber per 100g in grams"
    )
    sugar_per_100g: float = Field(
        0.0, ge=0, allow_inf_nan=False, description="Sugar per 100g in grams"
    )

    @property
    def calories_per_100g(self) -> float:
        """Derive calories from macros using fiber-aware formula."""
        return round(
            Macros.raw_total_calories(
                self.protein_per_100g,
                self.carbs_per_100g,
                self.fat_per_100g,
                self.fiber_per_100g,
            ),
            2,
        )

    class Config:
        json_schema_extra = {
            "example": {
                "protein_per_100g": 31.0,
                "carbs_per_100g": 0.0,
                "fat_per_100g": 3.6,
                "fiber_per_100g": 0.0,
                "sugar_per_100g": 0.0,
            }
        }


class NutritionOverrideRequest(BaseModel):
    """Absolute values that intentionally bypass nutrition recalculation."""

    calories: float = Field(..., ge=0, allow_inf_nan=False)
    protein: float = Field(..., ge=0, allow_inf_nan=False)
    carbs: float = Field(..., ge=0, allow_inf_nan=False)
    fat: float = Field(..., ge=0, allow_inf_nan=False)


class EditMealIngredientsRequest(BaseModel):
    """Request DTO for editing meal ingredients."""

    dish_name: Optional[str] = Field(
        None, min_length=0, max_length=200, description="Updated meal name"
    )
    created_at: Optional[datetime] = Field(
        None, description="Updated meal log timestamp"
    )
    meal_type: Optional[str] = Field(
        None, description="Updated meal type derived from the user's local log time"
    )
    nutrition_override: Optional[NutritionOverrideRequest] = Field(
        None, description="Independent meal-level nutrition values"
    )
    nutrition_contract_version: Optional[int] = Field(None)
    override_intent: Optional[Literal["user_entered"]] = Field(None)
    food_item_changes: list[FoodItemChangeRequest] = Field(
        default_factory=list,
        description="List of ingredient changes",
    )

    @model_validator(mode="after")
    def validate_has_change(self):
        if (
            self.dish_name is None
            and self.created_at is None
            and self.meal_type is None
            and self.nutrition_override is None
            and not self.food_item_changes
        ):
            raise ValueError("At least one meal edit field is required")
        return self

    @model_validator(mode="after")
    def validate_v2_actions(self):
        has_v2_fields = any(
            change.origin is not None
            or change.food_reference_id is not None
            or change.source_food_id is not None
            or change.source_namespace is not None
            or change.food_id is not None
            for change in self.food_item_changes
        )
        if self.nutrition_contract_version not in (None, 2):
            raise ValueError("unsupported nutrition_contract_version")
        if self.nutrition_contract_version != 2:
            if has_v2_fields or self.override_intent is not None:
                raise ValueError("v2 fields require nutrition_contract_version=2")
            return self
        if self.nutrition_override is not None:
            # The absolute override payload is itself the user's intent.
            self.override_intent = "user_entered"

        for change in self.food_item_changes:
            if change.nutrition_override is not None or change.clear_nutrition_override:
                change.override_intent = "user_entered"
            identity_fields = (
                change.food_reference_id,
                change.fdc_id,
                change.source_food_id,
                change.source_namespace,
                change.food_id,
            )
            if change.action == "remove":
                if not change.id:
                    raise ValueError("v2 remove requires an owned food item id")
                continue

            if not change.id and change.action == "update":
                raise ValueError("v2 update requires an owned item id")
            if change.action == "add":
                if change.origin is None:
                    raise ValueError("v2 add requires origin")
                _validate_change_origin(change)
                continue

            if change.nutrition_override is not None:
                if any(value is not None for value in identity_fields):
                    raise ValueError("item override cannot replace source nutrition")
            elif change.clear_nutrition_override:
                if any(value is not None for value in identity_fields):
                    raise ValueError("item clear cannot replace source nutrition")
            elif change.origin is not None:
                _validate_change_origin(change)
            elif change.origin is None:
                if any(value is not None for value in identity_fields):
                    raise ValueError("v2 quantity update cannot replace source")
                is_portion_update = change.action == "update" and (
                    change.quantity is not None or change.unit is not None
                )
                has_legacy_source_echo = any(
                    value is not None
                    for value in (change.name, change.custom_nutrition)
                )
                if is_portion_update and has_legacy_source_echo:
                    # Older clients echo source fields on portion updates. They
                    # cannot replace authoritative nutrition without an origin,
                    # so discard the echoes before the domain strategy runs.
                    change.name = None
                    change.custom_nutrition = None
                    continue
                if any(
                    value is not None
                    for value in (
                        change.name,
                        change.custom_nutrition,
                    )
                ):
                    raise ValueError("v2 quantity update cannot replace source")
        return self

    class Config:
        json_schema_extra = {
            "example": {
                "dish_name": "Updated Grilled Chicken Salad",
                "created_at": "2026-06-28T12:30:00Z",
                "meal_type": "lunch",
                "food_item_changes": [
                    {
                        "action": "update",
                        "id": "existing-uuid",
                        "quantity": 200.0,
                        "unit": "g",
                    },
                    {
                        "action": "add",
                        "fdc_id": 168462,
                        "name": "Mixed Greens",
                        "quantity": 100.0,
                        "unit": "g",
                    },
                ],
            }
        }


def _validate_change_origin(change: FoodItemChangeRequest) -> None:
    if change.food_id is not None:
        raise ValueError("v2 saves cannot provide deprecated aliases")
    if change.origin == "local":
        if change.food_reference_id is None or any(
            value is not None
            for value in (
                change.fdc_id,
                change.source_namespace,
                change.source_food_id,
                change.custom_nutrition,
            )
        ):
            raise ValueError("local origin requires only food_reference_id")
    elif change.origin == "usda":
        if change.fdc_id is None or any(
            value is not None
            for value in (
                change.food_reference_id,
                change.source_namespace,
                change.source_food_id,
                change.custom_nutrition,
            )
        ):
            raise ValueError("usda origin requires only fdc_id")
    elif change.origin == "provider":
        if change.source_food_id is None or any(
            value is not None
            for value in (
                change.fdc_id,
                change.food_reference_id,
                change.custom_nutrition,
            )
        ):
            raise ValueError("provider origin requires source_food_id")
    elif change.origin == "custom":
        if change.custom_nutrition is None or any(
            value is not None
            for value in (
                change.fdc_id,
                change.food_reference_id,
                change.source_namespace,
                change.source_food_id,
            )
        ):
            raise ValueError("custom origin requires only custom_nutrition")


class AttachMealPhotoRequest(BaseModel):
    """Request DTO for attaching an uploaded image to a meal."""

    image_id: str = Field(..., description="Cloudinary upload image UUID")
    image_url: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Cloudinary secure URL returned after upload",
    )
    image_format: Literal["jpeg", "png"] = Field(
        "jpeg", description="Uploaded image format"
    )
    size_bytes: int = Field(
        ..., gt=0, le=8 * 1024 * 1024, description="Uploaded image size in bytes"
    )


class AddCustomIngredientRequest(BaseModel):
    """Request DTO for adding custom ingredient to meal."""

    name: str = Field(
        ..., min_length=1, max_length=200, description="Custom ingredient name"
    )
    quantity: float = Field(..., gt=0, le=10000, description="Quantity amount")
    unit: str = Field(
        ..., min_length=1, max_length=20, description="Unit of measurement"
    )
    nutrition: CustomNutritionRequest = Field(
        ..., description="Nutrition data per 100g"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Homemade Vinaigrette",
                "quantity": 30.0,
                "unit": "ml",
                "nutrition": {
                    "protein_per_100g": 0.5,
                    "carbs_per_100g": 2.0,
                    "fat_per_100g": 44.0,
                },
            }
        }
