"""Apply a requested locale onto a meal before building a detail response."""

import logging
from dataclasses import replace

from src.api.mappers.meal_mapper import MealMapper

logger = logging.getLogger(__name__)


def without_requested_meal_translation(meal, language: str):
    """Keep incomplete locale data from leaking into a response."""
    translations = getattr(meal, "translations", None)
    if not translations or language not in translations:
        return meal
    remaining = {
        cached_language: translation
        for cached_language, translation in translations.items()
        if cached_language != language
    }
    return replace(meal, translations=remaining or None)


async def ensure_requested_meal_translation(
    *,
    meal,
    language: str,
    query,
    event_bus,
    meal_translation_service,
):
    """Materialize a missing locale without serving a partial translation."""
    if MealMapper.has_persisted_image_display_names(meal):
        return meal
    if language == "en":
        return meal

    all_food_items = getattr(getattr(meal, "nutrition", None), "food_items", None) or []
    # Tracked (catalog-linked) lines get their name from food_reference at
    # read time — exclude them so translation completeness is judged only on
    # the untracked lines that actually need meal-translation overlay.
    food_items = [
        item
        for item in all_food_items
        if getattr(item, "food_reference_id", None) is None
    ]
    instructions = getattr(meal, "instructions", None)
    if not meal.dish_name and not food_items and not instructions:
        return meal

    if MealMapper.has_direct_response_localization(meal, language):
        return meal

    expected_ingredient_count = sum(
        1 for item in food_items if getattr(item, "name", None)
    )
    expected_instruction_count = sum(
        1 for step in (instructions or []) if isinstance(step, (dict, str))
    )
    cached = (meal.translations or {}).get(language)
    if cached and cached.is_fully_cached(
        expected_ingredient_count=expected_ingredient_count,
        expected_instruction_count=expected_instruction_count,
    ):
        return meal
    if meal_translation_service is None:
        return without_requested_meal_translation(meal, language)

    try:
        translation = await meal_translation_service.translate_meal(
            meal=meal,
            dish_name=meal.dish_name or "",
            food_items=food_items,
            target_language=language,
            instructions=instructions,
        )
    except Exception as exc:
        logger.warning(
            "Meal translation on read failed meal=%s lang=%s error_type=%s",
            meal.meal_id,
            language,
            type(exc).__name__,
        )
        return without_requested_meal_translation(meal, language)

    if translation is None or not translation.is_fully_cached(
        expected_ingredient_count=expected_ingredient_count,
        expected_instruction_count=expected_instruction_count,
    ):
        logger.warning(
            "Meal translation remains incomplete meal=%s lang=%s",
            meal.meal_id,
            language,
        )
        return without_requested_meal_translation(meal, language)

    try:
        return await event_bus.send(query)
    except Exception as exc:
        logger.warning(
            "Meal translation reload failed meal=%s lang=%s error_type=%s",
            meal.meal_id,
            language,
            type(exc).__name__,
        )
        return without_requested_meal_translation(meal, language)
