"""Canonical AI nutrition output contracts.

These schemas validate AI output before it is mapped into domain nutrition
models. Text-parse contracts ignore advisory AI calories for compatibility;
provider-facing vision contracts reject them so calories stay backend-derived.
"""

from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from src.domain.model.nutrition import MAX_FOOD_ITEM_QUANTITY

MAX_AI_FOOD_ITEMS = 8
MAX_TEXT_PARSE_ITEMS = 20
MAX_AI_MACRO_GRAMS = 5000.0
PARSE_TEXT_PREPARATIONS = ("raw", "boiled", "baked", "fried", "mashed", "unknown")


def _strip_required_text(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be empty")
    return stripped


class AINutritionMacros(BaseModel):
    """Macronutrients reported by AI, in grams."""

    model_config = ConfigDict(extra="ignore")

    protein_g: float = Field(
        ...,
        ge=0,
        le=MAX_AI_MACRO_GRAMS,
        validation_alias=AliasChoices("protein_g", "protein"),
        description="Protein grams",
    )
    carbs_g: float = Field(
        ...,
        ge=0,
        le=MAX_AI_MACRO_GRAMS,
        validation_alias=AliasChoices("carbs_g", "carbs"),
        description="Carbohydrate grams",
    )
    fat_g: float = Field(
        ...,
        ge=0,
        le=MAX_AI_MACRO_GRAMS,
        validation_alias=AliasChoices("fat_g", "fat"),
        description="Fat grams",
    )
    fiber_g: float = Field(
        0.0,
        ge=0,
        le=MAX_AI_MACRO_GRAMS,
        validation_alias=AliasChoices("fiber_g", "fiber"),
        description="Fiber grams",
    )
    sugar_g: float = Field(
        0.0,
        ge=0,
        le=MAX_AI_MACRO_GRAMS,
        validation_alias=AliasChoices("sugar_g", "sugar"),
        description="Sugar grams",
    )


class AINutrientMicros(BaseModel):
    """Optional NRF-related micros. Omit unknown values; never invent."""

    model_config = ConfigDict(extra="ignore")

    vitamin_a: float | None = Field(None, ge=0)
    vitamin_c: float | None = Field(None, ge=0)
    vitamin_e: float | None = Field(None, ge=0)
    calcium: float | None = Field(None, ge=0)
    iron: float | None = Field(None, ge=0)
    magnesium: float | None = Field(None, ge=0)
    potassium: float | None = Field(None, ge=0)
    sodium: float | None = Field(None, ge=0)
    saturated_fat: float | None = Field(None, ge=0)
    added_sugar: float | None = Field(None, ge=0)


class AIVisionNutritionMacros(AINutritionMacros):
    """Strict macronutrients contract for provider-facing vision responses."""

    model_config = ConfigDict(extra="forbid")


class VisionFoodEstimate(BaseModel):
    """Single food estimate extracted from an image."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    localized_name: str | None = Field(None, max_length=200)
    quantity_g: float = Field(
        ...,
        gt=0,
        le=MAX_FOOD_ITEM_QUANTITY,
        validation_alias=AliasChoices("quantity_g", "quantity"),
    )
    macros: AIVisionNutritionMacros
    micros: AINutrientMicros | None = None
    confidence: float = Field(1.0, ge=0, le=1)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _strip_required_text(value)


class BeverageMetadata(BaseModel):
    """Metadata for packaged beverage images detected by AI."""

    model_config = ConfigDict(extra="forbid")

    is_packaged_beverage: bool
    brand: str | None = Field(None, max_length=100)
    product_name: str | None = Field(None, max_length=100)
    container_type: Literal["can", "bottle", "cup", "carton", "unknown"] = "unknown"
    volume_ml: int | None = None
    sugar_per_100ml: float | None = Field(None, ge=0)
    kcal_per_100ml: float | None = Field(None, ge=0)
    label_source: Literal["nutrition_panel", "front_label", "estimate"] = "estimate"


class FoodLabelServingSize(BaseModel):
    """Serving-size details read from a packaged nutrition label."""

    model_config = ConfigDict(extra="forbid")

    display_text: str = Field(..., min_length=1, max_length=80)
    grams: float = Field(..., gt=0, le=MAX_FOOD_ITEM_QUANTITY)

    @field_validator("display_text")
    @classmethod
    def validate_display_text(cls, value: str) -> str:
        return _strip_required_text(value)


class FoodLabelNutritionResponse(BaseModel):
    """Structured nutrition facts label response."""

    model_config = ConfigDict(extra="forbid")

    is_food_label: bool = Field(
        True, description="Whether a Nutrition Facts label is visible"
    )
    product_name: str = Field("Scanned Food Label", min_length=1, max_length=200)
    brand: str | None = Field(None, max_length=100)
    serving_size: FoodLabelServingSize = Field(
        default_factory=lambda: FoodLabelServingSize(
            display_text="100g",
            grams=100,
        )
    )
    servings_per_package: float = Field(1, gt=0, le=1000)
    label_calories_per_serving: float | None = Field(None, ge=0, le=5000)
    macros_per_serving: AIVisionNutritionMacros
    micros_per_serving: AINutrientMicros | None = None
    confidence: float = Field(0.5, ge=0, le=1)
    label_notes: list[str] = Field(default_factory=list)

    @field_validator("product_name", mode="before")
    @classmethod
    def default_product_name(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "Scanned Food Label"
        return value

    @field_validator("serving_size", mode="before")
    @classmethod
    def default_serving_size(cls, value: Any) -> Any:
        if value is None:
            return {"display_text": "100g", "grams": 100}
        return value

    @field_validator("servings_per_package", mode="before")
    @classmethod
    def default_servings_per_package(cls, value: Any) -> Any:
        if value is None:
            return 1
        return value

    @field_validator("product_name", "brand")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _strip_required_text(value)

    @field_validator("label_notes", mode="before")
    @classmethod
    def normalize_label_notes(cls, value: Any) -> Any:
        if value is None or not isinstance(value, list):
            return value

        notes: list[Any] = []
        for note in value:
            if isinstance(note, str):
                stripped = note.strip()
                if not stripped:
                    continue
                notes.append(stripped)
            else:
                notes.append(note)

        return notes


class VisionNutritionResponse(BaseModel):
    """Structured image meal-analysis response."""

    model_config = ConfigDict(extra="forbid")

    is_food: bool = Field(True, description="Whether the image contains edible food")
    dish_name: str | None = Field(None, max_length=200)
    localized_language: str | None = Field(None, max_length=8)
    localized_dish_name: str | None = Field(None, max_length=200)
    emoji: str | None = Field(None, max_length=32)
    foods: list[VisionFoodEstimate] = Field(
        default_factory=list,
        max_length=MAX_AI_FOOD_ITEMS,
        description="Foods visible in the image",
    )
    confidence: float = Field(0.5, ge=0, le=1)
    beverage_metadata: BeverageMetadata | None = None

    @model_validator(mode="after")
    def require_foods_for_food_images(self) -> "VisionNutritionResponse":
        if self.beverage_metadata is not None:
            raise ValueError(
                "beverage_metadata is not accepted for meal scan output; "
                "drinks must be represented as normal foods"
            )
        if self.is_food and not self.foods:
            raise ValueError(
                "foods must contain at least one item when is_food is true"
            )
        return self


class MealTextFoodEstimate(BaseModel):
    """Single item parsed from natural-language meal text."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=200)
    lookup_name: str | None = Field(None, max_length=200)
    preparation: str = Field("unknown", max_length=20)
    quantity: float = Field(..., gt=0, le=MAX_FOOD_ITEM_QUANTITY)
    unit: str = Field(..., min_length=1, max_length=50)
    english_unit: str | None = Field(None, max_length=50)
    quantity_g: float | None = Field(None, gt=0, le=MAX_FOOD_ITEM_QUANTITY)
    macros: AINutritionMacros
    micros: AINutrientMicros | None = None

    @model_validator(mode="before")
    @classmethod
    def fold_flat_macros(cls, data: Any) -> Any:
        """Accept the current text prompt shape while storing canonical macros."""
        if not isinstance(data, dict) or "macros" in data:
            return data

        flat_macros = {
            key: data[key]
            for key in (
                "protein_g",
                "protein",
                "carbs_g",
                "carbs",
                "fat_g",
                "fat",
                "fiber_g",
                "fiber",
                "sugar_g",
                "sugar",
            )
            if key in data
        }
        if flat_macros:
            data = dict(data)
            data["macros"] = flat_macros
        return data

    @field_validator("name", "unit", "english_unit")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _strip_required_text(value)

    @field_validator("lookup_name")
    @classmethod
    def validate_lookup_name(cls, value: str | None) -> str | None:
        return _strip_required_text(value) if value is not None else None

    @field_validator("preparation", mode="before")
    @classmethod
    def normalize_preparation(cls, value: Any) -> str:
        normalized = str(value or "unknown").strip().lower()
        return normalized if normalized in PARSE_TEXT_PREPARATIONS else "unknown"


class LocalizedFoodNameBatch(BaseModel):
    """Ordered display-name translations for leftover English parse-text names."""

    model_config = ConfigDict(extra="ignore")

    items: list[str] = Field(..., min_length=1, max_length=MAX_TEXT_PARSE_ITEMS)

    @model_validator(mode="before")
    @classmethod
    def fold_named_items(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "items" not in data:
            return data
        folded: list[Any] = []
        for item in data.get("items") or []:
            if isinstance(item, str):
                folded.append(item)
            elif isinstance(item, dict) and item.get("name"):
                folded.append(item["name"])
            else:
                folded.append(item)
        payload = dict(data)
        payload["items"] = folded
        return payload

    @field_validator("items")
    @classmethod
    def strip_names(cls, value: list[str]) -> list[str]:
        stripped = [str(item).strip() for item in value]
        if any(not item for item in stripped):
            raise ValueError("translated names must not be empty")
        return stripped


class MealTextNutritionResponse(BaseModel):
    """Structured natural-language meal parse response."""

    model_config = ConfigDict(extra="ignore")

    emoji: str | None = Field(None, max_length=16)
    items: list[MealTextFoodEstimate] = Field(
        ...,
        min_length=1,
        max_length=MAX_TEXT_PARSE_ITEMS,
    )
