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

logger = logging.getLogger(__name__)

_CATALOG_TRANSLATION_CACHE: TTLCache[tuple[str, str], str] = TTLCache(
    maxsize=4096, ttl=6 * 3600
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


async def localize_catalog_meals(
    meals: Iterable[CatalogMeal],
    *,
    language: str,
    translation_service: TextTranslationService | None,
    include_ingredients: bool = True,
) -> tuple[CatalogMeal, ...]:
    """Return localized presentation copies of catalog meals."""

    original = tuple(meals)
    if language == "en" or translation_service is None or not original:
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
    texts = _unique_display_texts(
        unique_meals.values(), include_ingredients=include_ingredients
    )
    if not texts:
        return dict(unique_meals)

    translations: dict[str, str] = {}
    missing: list[str] = []
    for text in texts:
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
            logger.warning(
                "catalog response translation failed language=%s error_type=%s",
                language,
                type(exc).__name__,
            )
            if not translations:
                return None

    if missing and not translations:
        return None

    for text in texts:
        translations.setdefault(text, text)
    return {
        meal_id: _replace_meal_display_text(meal, translations)
        for meal_id, meal in unique_meals.items()
    }


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
