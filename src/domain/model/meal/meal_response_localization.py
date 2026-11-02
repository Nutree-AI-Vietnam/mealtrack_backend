"""Validated localized fields returned by and persisted from meal analysis."""

from dataclasses import dataclass, replace
from typing import Any

from src.domain.constants.languages import normalize_language
from src.domain.exceptions.ai_exceptions import MealResponseLocalizationError
from src.domain.model.meal.meal import Meal


@dataclass(frozen=True)
class MealResponseLocalization:
    """Localized display fields aligned with canonical food-item order."""

    language: str
    dish_name: str
    food_item_names: tuple[str, ...]


def parse_meal_response_localization(
    structured_data: dict[str, Any] | None,
    target_language: str | None,
    *,
    expected_food_count: int | None = None,
) -> MealResponseLocalization | None:
    """Validate and extract same-call localized fields for a meal response."""
    language = normalize_language(target_language)
    if language == "en":
        return None
    if not isinstance(structured_data, dict):
        raise MealResponseLocalizationError(
            "Localized meal output is missing structured data"
        )

    reported_language_value = structured_data.get("localized_language")
    if not isinstance(reported_language_value, str):
        raise MealResponseLocalizationError(
            "Localized meal output is missing its language"
        )
    reported_language = normalize_language(reported_language_value)
    if reported_language != language:
        raise MealResponseLocalizationError(
            "Localized meal output language does not match the requested language"
        )

    dish_name = structured_data.get("localized_dish_name")
    foods = structured_data.get("foods")
    if not isinstance(dish_name, str) or not dish_name.strip():
        raise MealResponseLocalizationError(
            "Localized meal output is missing the dish name"
        )
    if not isinstance(foods, list):
        raise MealResponseLocalizationError(
            "Localized meal output is missing food items"
        )
    if expected_food_count is not None and len(foods) != expected_food_count:
        raise MealResponseLocalizationError(
            "Localized meal output does not cover every food item"
        )

    names: list[str] = []
    for food in foods:
        localized_name = food.get("localized_name") if isinstance(food, dict) else None
        if not isinstance(localized_name, str) or not localized_name.strip():
            raise MealResponseLocalizationError(
                "Localized meal output is missing a food item name"
            )
        names.append(localized_name.strip())

    return MealResponseLocalization(
        language=language,
        dish_name=dish_name.strip(),
        food_item_names=tuple(names),
    )


def localize_food_items(
    food_items: list[Any],
    localization: MealResponseLocalization,
) -> list[Any]:
    """Rename food items, in order, to their requested-language names."""
    if len(food_items) != len(localization.food_item_names):
        raise MealResponseLocalizationError(
            "Localized names do not cover every persisted food item"
        )
    return [
        replace(item, name=localized_name)
        for item, localized_name in zip(
            food_items,
            localization.food_item_names,
            strict=True,
        )
    ]


def persist_meal_response_localization(
    meal: Meal,
    localization: MealResponseLocalization | None,
) -> Meal:
    """Persist requested-language display names without changing nutrition."""
    if localization is None:
        return meal

    nutrition = meal.nutrition
    if nutrition is None or not nutrition.food_items:
        return replace(meal, dish_name=localization.dish_name)

    return replace(
        meal,
        dish_name=localization.dish_name,
        nutrition=replace(
            nutrition,
            food_items=localize_food_items(nutrition.food_items, localization),
        ),
    )
