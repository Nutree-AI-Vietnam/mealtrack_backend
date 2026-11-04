"""21-key micronutrient variants of the scan contracts.

Today's photo, label and text contracts ask for ten micros. With
``AI_SCAN_FULL_MICROS_ENABLED`` the provider gets these subclasses instead,
which add the eleven vitamins and minerals scans never reported. Parsers always
read the wide variants, so ten-key and 21-key answers both validate and a
missing key still means unknown.
"""

from pydantic import BaseModel, Field

from src.domain.model.ai.nutrition_contracts import (
    MAX_AI_FOOD_ITEMS,
    MAX_TEXT_PARSE_ITEMS,
    AINutrientMicros,
    FoodLabelNutritionResponse,
    MealTextFoodEstimate,
    MealTextNutritionResponse,
    VisionFoodEstimate,
    VisionNutritionResponse,
)

FULL_MICROS_ADDED_KEYS = (
    "vitamin_d",
    "vitamin_k",
    "thiamin",
    "riboflavin",
    "niacin",
    "vitamin_b6",
    "vitamin_b12",
    "folate",
    "phosphorus",
    "zinc",
    "selenium",
)


class AINutrientMicrosFull(AINutrientMicros):
    """All 21 micros keys, in the units of ``Micros``. Omit unknown values."""

    vitamin_d: float | None = Field(None, ge=0)
    vitamin_k: float | None = Field(None, ge=0)
    thiamin: float | None = Field(None, ge=0)
    riboflavin: float | None = Field(None, ge=0)
    niacin: float | None = Field(None, ge=0)
    vitamin_b6: float | None = Field(None, ge=0)
    vitamin_b12: float | None = Field(None, ge=0)
    folate: float | None = Field(None, ge=0)
    phosphorus: float | None = Field(None, ge=0)
    zinc: float | None = Field(None, ge=0)
    selenium: float | None = Field(None, ge=0)


class VisionFoodEstimateFull(VisionFoodEstimate):
    micros: AINutrientMicrosFull | None = None


class VisionNutritionResponseFull(VisionNutritionResponse):
    # Pydantic validates the narrowed rows; mypy sees list invariance only.
    foods: list[VisionFoodEstimateFull] = Field(  # type: ignore[assignment]
        default_factory=list,
        max_length=MAX_AI_FOOD_ITEMS,
        description="Foods visible in the image",
    )


class FoodLabelNutritionResponseFull(FoodLabelNutritionResponse):
    micros_per_serving: AINutrientMicrosFull | None = None


class MealTextFoodEstimateFull(MealTextFoodEstimate):
    micros: AINutrientMicrosFull | None = None


class MealTextNutritionResponseFull(MealTextNutritionResponse):
    items: list[MealTextFoodEstimateFull] = Field(  # type: ignore[assignment]
        ...,
        min_length=1,
        max_length=MAX_TEXT_PARSE_ITEMS,
    )


_FULL_VARIANTS: dict[type[BaseModel], type[BaseModel]] = {
    VisionNutritionResponse: VisionNutritionResponseFull,
    FoodLabelNutritionResponse: FoodLabelNutritionResponseFull,
    MealTextNutritionResponse: MealTextNutritionResponseFull,
}


def scan_schema(schema: type[BaseModel], *, full_micros: bool) -> type[BaseModel]:
    """The contract to give the provider: the 21-key variant when full micros
    are on, else today's ten-key contract unchanged."""
    return _FULL_VARIANTS[schema] if full_micros else schema
