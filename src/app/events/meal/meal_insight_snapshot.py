"""Bounded meal snapshot embedded on generic meal Queue events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.domain.constants.languages import resolve_app_locale
from src.domain.model.meal import Meal
from src.domain.model.nutrition.extra_nutrients import (
    food_item_effective_micros,
    merge_meal_micros,
)
from src.domain.model.nutrition.micros import Micros
from src.domain.services.prompts.prompt_constants import LANGUAGE_NAMES
from src.domain.utils.timezone_utils import utc_now

_PROFILE_CONTEXT_KEYS = ("fitness_goal", "allergies", "dietary_preferences")


class MealInsightNutrition(BaseModel):
    """Nutrition snapshot embedded in a meal integration event."""

    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: float
    sugar_g: float
    confidence_score: float | None = None
    micros: dict[str, float] | None = None


class MealInsightIngredient(BaseModel):
    """Ingredient snapshot embedded in a meal integration event."""

    id: str
    name: str
    quantity: float
    unit: str
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: float
    sugar_g: float
    confidence: float | None = None
    micros: dict[str, float] | None = None


class MealInsightSnapshot(BaseModel):
    """Bounded meal snapshot consumed by the Worker business handler."""

    dish_name: str | None = None
    language: str = "en"
    nutrition: MealInsightNutrition
    ingredients: list[MealInsightIngredient] = Field(default_factory=list)
    user_context: dict[str, Any] | None = None
    tokens: list[str] | None = None

    @classmethod
    def from_meal(
        cls,
        meal: Meal,
        *,
        language: str = "en",
        user_context: dict[str, Any] | None = None,
        tokens: list[str] | None = None,
    ) -> MealInsightSnapshot:
        """Build the bounded Worker input from the authoritative meal."""
        if meal.nutrition is None:
            raise ValueError("Meal insight events require nutrition")

        nutrition = meal.nutrition
        macros = getattr(nutrition, "effective_macros", None) or getattr(
            nutrition, "macros", None
        )
        if macros is None:
            raise ValueError("Meal insight events require nutrition macros")
        meal_micros = compact_insight_micros(
            merge_meal_micros(
                getattr(nutrition, "micros", None),
                getattr(nutrition, "food_items", None),
            )
        )
        ingredients = [
            _insight_ingredient(item)
            for item in (getattr(nutrition, "food_items", None) or [])[:8]
            if _item_macros(item) is not None
        ]
        calories = getattr(nutrition, "calories", None)
        if calories is None:
            calories = getattr(macros, "total_calories", 0) or 0
        return cls(
            dish_name=meal.dish_name,
            language=insight_language_code(language),
            nutrition=MealInsightNutrition(
                calories=float(calories),
                protein_g=float(macros.protein),
                carbs_g=float(macros.carbs),
                fat_g=float(macros.fat),
                fiber_g=float(getattr(macros, "fiber", 0) or 0),
                sugar_g=float(getattr(macros, "sugar", 0) or 0),
                confidence_score=_optional_float(
                    getattr(nutrition, "confidence_score", None)
                ),
                micros=meal_micros,
            ),
            ingredients=ingredients,
            user_context=user_context,
            tokens=tokens,
        )


def insight_language_code(language: str | None) -> str:
    """Normalize a meal-insight language to an enabled app locale."""
    return resolve_app_locale(language)


def insight_language_name(language: str | None) -> str:
    """Return the English language name used in insight prompts."""
    return LANGUAGE_NAMES.get(insight_language_code(language), "English")


def _item_macros(item: Any) -> Any:
    return getattr(item, "effective_macros", None) or getattr(item, "macros", None)


def _optional_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _insight_ingredient(item: Any) -> MealInsightIngredient:
    macros = _item_macros(item)
    return MealInsightIngredient(
        id=str(getattr(item, "id", "")),
        name=str(getattr(item, "name", "")),
        quantity=float(getattr(item, "quantity", 0) or 0),
        unit=str(getattr(item, "unit", "") or "g"),
        calories=float(getattr(item, "calories", 0) or 0),
        protein_g=float(macros.protein),
        carbs_g=float(macros.carbs),
        fat_g=float(macros.fat),
        fiber_g=float(getattr(macros, "fiber", 0) or 0),
        sugar_g=float(getattr(macros, "sugar", 0) or 0),
        confidence=_optional_float(getattr(item, "confidence", None)),
        micros=compact_insight_micros(food_item_effective_micros(item)),
    )


def compact_insight_micros(micros: Micros | None) -> dict[str, float] | None:
    """Keep logged micros only, rounded for the Queue snapshot."""
    if not isinstance(micros, Micros):
        return None
    compact = {
        key: round(float(value), 1) for key, value in micros.to_dict().items()
    }
    return compact or None


def meal_insight_occurred_at(meal: Meal) -> datetime:
    """Return the stable meal snapshot timestamp shared by both Queue events."""
    for attribute in ("updated_at", "ready_at", "created_at"):
        value = getattr(meal, attribute, None)
        if isinstance(value, datetime):
            return value
    return utc_now()


def compact_insight_user_context(result: Any) -> dict[str, Any] | None:
    """Keep only the bounded profile fields the Worker prompt needs."""
    if not isinstance(result, dict):
        return None
    profile = result.get("profile") if isinstance(result.get("profile"), dict) else {}
    tdee = result.get("tdee") if isinstance(result.get("tdee"), dict) else {}
    context: dict[str, Any] = {
        key: profile[key] for key in _PROFILE_CONTEXT_KEYS if key in profile
    }
    language_code = result.get("language_code") or profile.get("language_code")
    if language_code:
        context["language_code"] = insight_language_code(str(language_code))
    if "target_calories" in tdee:
        context["target_calories"] = tdee["target_calories"]
    return context or None
