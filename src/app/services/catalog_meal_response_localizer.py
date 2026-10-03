"""Request-language localization for catalog meal response projections."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import replace
from typing import Any, Protocol

from cachetools import TTLCache

from src.app.services.food_name_localizer import (
    translate_for_presentation,
    translation_is_cacheable,
)
from src.domain.constants.translation_limits import iter_translation_batches
from src.domain.model.meal_recommendation import (
    CatalogMeal,
    PersistedMealRecommendationCandidate,
    PersistedMealRecommendationPlan,
    PersistedMealRecommendationSlot,
)
from src.domain.model.translation_result import TranslationOutcome
from src.planner_observability import planner_timed

logger = logging.getLogger(__name__)

_CATALOG_TRANSLATION_CACHE: TTLCache[tuple[str, str], str] = TTLCache(
    maxsize=4096, ttl=6 * 3600
)
_VIETNAMESE_CHARACTERS = frozenset(
    "ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ"
)


def clear_catalog_presentation_cache() -> None:
    """Reset process-local catalog presentation translations. Tests only."""

    _CATALOG_TRANSLATION_CACHE.clear()


class TextTranslationService(Protocol):
    """Minimal translation dependency needed by catalog response localization."""

    async def translate_texts(self, texts: list[str], *args: str) -> Any: ...


async def localize_meal_recommendation_plan(
    plan: PersistedMealRecommendationPlan,
    *,
    language: str,
    translation_service: TextTranslationService | None,
) -> PersistedMealRecommendationPlan:
    """Return a localized presentation copy of a recommendation plan."""

    if language == "en" or translation_service is None:
        return plan

    meals = [meal for slot in plan.slots for meal in _slot_catalog_meals(slot)]
    localized_meals = await _localized_meals(
        meals,
        language=language,
        translation_service=translation_service,
    )
    if localized_meals is None:
        return plan

    return replace(
        plan,
        slots=tuple(
            _replace_slot_catalog_meals(slot, localized_meals) for slot in plan.slots
        ),
    )


@planner_timed("localization")
async def localize_catalog_meals(
    meals: Iterable[CatalogMeal],
    *,
    language: str,
    translation_service: TextTranslationService | None,
    include_ingredients: bool = True,
) -> tuple[CatalogMeal, ...]:
    """Return localized presentation copies of catalog meals."""

    original = tuple(meals)
    if translation_service is None or not original:
        return original
    persisted_loader = getattr(translation_service, "get_catalog_translations", None)
    if callable(persisted_loader):
        mappings = await persisted_loader(original, language)
        return tuple(
            _replace_meal_display_text(meal, mappings.get(meal.id, {}))
            for meal in original
        )

    if language == "en":
        return original
    localized_meals = await _localized_meals(
        list(original),
        language=language,
        translation_service=translation_service,
        include_ingredients=include_ingredients,
    )
    if localized_meals is None:
        return original
    return tuple(localized_meals.get(meal.id, meal) for meal in original)


@planner_timed("localization")
async def localize_catalog_meal_names(
    meals: Iterable[CatalogMeal],
    *,
    language: str,
    translation_service: TextTranslationService | None,
) -> tuple[CatalogMeal, ...]:
    """Localize only the names needed by compact weekly-plan summaries."""
    original = tuple(meals)
    if translation_service is None or not original:
        return original
    persisted_loader = getattr(translation_service, "get_catalog_translations", None)
    if callable(persisted_loader):
        mappings = await persisted_loader(original, language)
        return tuple(
            replace(meal, name=mappings.get(meal.id, {}).get(meal.name, meal.name))
            for meal in original
        )

    if language == "en":
        return original
    summary_meals = [
        replace(
            meal,
            cuisine="",
            description=None,
            summary=None,
            equipment=None,
            tag=None,
            allergens=None,
            ingredients=(),
            steps=(),
        )
        for meal in original
    ]
    localized = await _localized_meals(
        summary_meals,
        language=language,
        translation_service=translation_service,
        include_ingredients=False,
    )
    if localized is None:
        return original
    return tuple(
        replace(meal, name=localized.get(meal.id, meal).name) for meal in original
    )


async def localize_presentation_texts(
    texts: Iterable[str],
    *,
    language: str,
    translation_service: TextTranslationService | None,
) -> tuple[str, ...]:
    """Translate short user-facing copy, preserving source text on failure."""
    original = tuple(texts)
    if translation_service is None or not original:
        return original
    result = await translate_for_presentation(translation_service, original, language)
    if result.outcome is not TranslationOutcome.TRANSLATED:
        return original
    return tuple(result.items)


async def localize_meal_recommendation_slot(
    slot: PersistedMealRecommendationSlot,
    *,
    language: str,
    translation_service: TextTranslationService | None,
) -> PersistedMealRecommendationSlot:
    """Return a localized presentation copy of one recommendation slot."""

    if language == "en" or translation_service is None:
        return slot

    localized_meals = await _localized_meals(
        list(_slot_catalog_meals(slot)),
        language=language,
        translation_service=translation_service,
    )
    if localized_meals is None:
        return slot

    return _replace_slot_catalog_meals(slot, localized_meals)


def _slot_catalog_meals(
    slot: PersistedMealRecommendationSlot,
) -> tuple[CatalogMeal, ...]:
    candidates = (slot.selected, *slot.alternatives)
    return tuple(
        candidate.catalog_meal
        for candidate in candidates
        if candidate is not None and candidate.catalog_meal is not None
    )


async def _localized_meals(
    meals: list[CatalogMeal],
    *,
    language: str,
    translation_service: TextTranslationService,
    include_ingredients: bool = True,
) -> dict[str, CatalogMeal] | None:
    unique_meals = {meal.id: meal for meal in meals}
    texts_by_meal = {
        meal.id: _unique_display_texts((meal,), include_ingredients=include_ingredients)
        for meal in unique_meals.values()
    }
    texts = list(
        dict.fromkeys(text for values in texts_by_meal.values() for text in values)
    )
    if not texts:
        return dict(unique_meals)

    translations: dict[str, str] = {}
    missing: list[str] = []
    for text in texts:
        if language == "vi" and _is_vietnamese_text(text):
            translations[text] = text
            continue
        cached = _CATALOG_TRANSLATION_CACHE.get((language, text))
        if cached is not None:
            translations[text] = cached
        else:
            missing.append(text)

    if missing:
        try:
            for batch in iter_translation_batches(missing):
                result = await translate_for_presentation(
                    translation_service, batch, language
                )
                if result.outcome is not TranslationOutcome.TRANSLATED:
                    continue
                cacheable = translation_is_cacheable(result)
                if len(result.items) != len(batch):
                    continue
                for index, text in enumerate(batch):
                    translated = result.items[index]
                    translations[text] = translated
                    if cacheable:
                        _CATALOG_TRANSLATION_CACHE[(language, text)] = translated
        except Exception as exc:
            logger.warning(
                "catalog response translation failed language=%s error_type=%s",
                language,
                type(exc).__name__,
            )

    localized: dict[str, CatalogMeal] = {}
    for meal_id, meal in unique_meals.items():
        meal_texts = texts_by_meal[meal_id]
        if any(text not in translations for text in meal_texts):
            localized[meal_id] = meal
            continue
        localized[meal_id] = _replace_meal_display_text(meal, translations)

    if all(localized[meal_id] is meal for meal_id, meal in unique_meals.items()):
        return None
    return localized


def _is_vietnamese_text(value: str) -> bool:
    """Avoid treating Vietnamese catalog copy as English source text."""

    return any(character.casefold() in _VIETNAMESE_CHARACTERS for character in value)


def _unique_display_texts(
    meals: Iterable[CatalogMeal],
    *,
    include_ingredients: bool = True,
) -> list[str]:
    texts: list[str] = []
    for meal in meals:
        _append_unique(texts, meal.name)
        _append_unique(texts, meal.cuisine)
        if meal.description:
            _append_unique(texts, meal.description)
        if meal.summary:
            _append_unique(texts, meal.summary)
        if meal.equipment:
            _append_unique(texts, meal.equipment)
        if meal.tag:
            _append_unique(texts, meal.tag)
        if meal.allergens:
            _append_unique(texts, meal.allergens)
        for step in meal.steps:
            _append_unique(texts, step.title)
            _append_unique(texts, step.description)
        if include_ingredients:
            for ingredient in meal.ingredients:
                _append_unique(texts, ingredient.display_name)
    return texts


def _append_unique(texts: list[str], value: str) -> None:
    if value and value not in texts:
        texts.append(value)


def _replace_meal_display_text(
    meal: CatalogMeal,
    translations: dict[str, str],
) -> CatalogMeal:
    return replace(
        meal,
        name=translations.get(meal.name, meal.name),
        cuisine=translations.get(meal.cuisine, meal.cuisine),
        description=(
            translations.get(meal.description, meal.description)
            if meal.description
            else None
        ),
        summary=(
            translations.get(meal.summary, meal.summary) if meal.summary else None
        ),
        equipment=(
            translations.get(meal.equipment, meal.equipment) if meal.equipment else None
        ),
        tag=translations.get(meal.tag, meal.tag) if meal.tag else None,
        allergens=(
            translations.get(meal.allergens, meal.allergens) if meal.allergens else None
        ),
        ingredients=tuple(
            replace(
                ingredient,
                display_name=translations.get(
                    ingredient.display_name,
                    ingredient.display_name,
                ),
            )
            for ingredient in meal.ingredients
        ),
        steps=tuple(
            replace(
                step,
                title=translations.get(step.title, step.title),
                description=translations.get(step.description, step.description),
            )
            for step in meal.steps
        ),
    )


def _replace_slot_catalog_meals(
    slot: PersistedMealRecommendationSlot,
    localized_meals: dict[str, CatalogMeal],
) -> PersistedMealRecommendationSlot:
    return replace(
        slot,
        selected=(
            _replace_candidate_catalog_meal(slot.selected, localized_meals)
            if slot.selected is not None
            else None
        ),
        alternatives=tuple(
            _replace_candidate_catalog_meal(candidate, localized_meals)
            for candidate in slot.alternatives
        ),
    )


def _replace_candidate_catalog_meal(
    candidate: PersistedMealRecommendationCandidate,
    localized_meals: dict[str, CatalogMeal],
) -> PersistedMealRecommendationCandidate:
    if candidate.catalog_meal is None:
        return candidate
    return replace(
        candidate,
        catalog_meal=localized_meals.get(
            candidate.catalog_meal.id, candidate.catalog_meal
        ),
    )


@planner_timed("localization")
async def localize_grocery_categories(
    categories: tuple[Any, ...] | list[Any],
    *,
    language: str,
    translation_service: TextTranslationService | None,
) -> tuple[Any, ...]:
    """Translate item names in grocery categories when language is non-English."""
    if language == "en" or translation_service is None or not categories:
        return tuple(categories)

    persisted_loader = getattr(translation_service, "get_grocery_translations", None)
    if callable(persisted_loader):
        persisted_translations = await persisted_loader(categories, language)
        return tuple(
            replace(
                cat,
                items=tuple(
                    replace(item, name=persisted_translations.get(item.name, item.name))
                    for item in cat.items
                ),
            )
            for cat in categories
        )

    item_names = [item.name for cat in categories for item in cat.items if item.name]
    if not item_names:
        return tuple(categories)

    translations: dict[str, str] = {}
    missing: list[str] = []
    for text in set(item_names):
        if language == "vi" and _is_vietnamese_text(text):
            translations[text] = text
            continue
        cached = _CATALOG_TRANSLATION_CACHE.get((language, text))
        if cached is not None:
            translations[text] = cached
        else:
            missing.append(text)

    if missing:
        try:
            for batch in iter_translation_batches(missing):
                result = await translate_for_presentation(
                    translation_service, batch, language
                )
                if result.outcome is TranslationOutcome.UNAVAILABLE:
                    continue
                cacheable = translation_is_cacheable(result)
                for index, text in enumerate(batch):
                    translated = (
                        result.items[index] if index < len(result.items) else text
                    )
                    translations[text] = translated
                    if cacheable:
                        _CATALOG_TRANSLATION_CACHE[(language, text)] = translated
        except Exception as exc:
            logger.warning("grocery localization failed: %s", exc)

    return tuple(
        replace(
            cat,
            items=tuple(
                replace(item, name=translations.get(item.name, item.name))
                for item in cat.items
            ),
        )
        for cat in categories
    )
