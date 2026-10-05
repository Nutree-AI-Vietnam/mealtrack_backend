"""Source-first micronutrient enrichment for catalog recipes."""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.nutrition.extra_nutrients import extra_nutrients_to_micros
from src.domain.model.nutrition.micros import Micros

logger = logging.getLogger(__name__)
MICRONUTRIENT_FIELDS = tuple(Micros.__dataclass_fields__)
MicronutrientEstimator = Callable[
    [CatalogMeal, tuple[str, ...], dict[str, float]], Awaitable[dict[str, Any]]
]
FdcMicronutrientLoader = Callable[[list[int]], Awaitable[dict[int, dict[str, Any]]]]


class CatalogRecipeMicronutrientEnrichmentService:
    """Load USDA reference nutrients, then cache AI estimates for remaining fields."""

    def __init__(
        self,
        uow_factory,
        *,
        estimator: MicronutrientEstimator | None = None,
        fdc_loader: FdcMicronutrientLoader | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._estimator = estimator
        self._fdc_loader = fdc_loader

    async def compute(self, preparation):
        """Compute a staged worker result without claims, sessions or persistence."""
        from src.app.services.catalog_micronutrient_computer import (
            compute_micronutrients,
        )

        return await compute_micronutrients(
            preparation, estimator=self._estimator, fdc_loader=self._fdc_loader
        )

    async def load_cached(self, meal: CatalogMeal) -> CatalogMeal:
        """Overlay persisted estimates without claiming work or calling providers."""
        async with self._uow_factory() as uow:
            cached = await uow.catalog_preparation.get_overlay(meal.id)
        source_labels = await self._reference_sources(meal)
        loaded = _has_complete_micronutrients(meal, cached)
        return _with_estimate(meal, cached, source_labels, loaded=loaded)

    async def _reference_sources(self, meal: CatalogMeal) -> dict[str, str]:
        values = meal.nutrition_micros.to_dict() if meal.nutrition_micros else {}
        if not values:
            return {}
        ids = [
            int(ingredient.food_reference_id)
            for ingredient in meal.ingredients
            if ingredient.food_reference_id is not None
        ]
        if not ids:
            return {}
        try:
            async with self._uow_factory() as uow:
                references = await uow.food_references.get_by_ids(ids)
        except Exception:
            return dict.fromkeys(values, "food_reference")
        references_by_id = {int(reference["id"]): reference for reference in references}
        ingredient_reference_ids = [
            int(ingredient.food_reference_id)
            for ingredient in meal.ingredients
            if ingredient.food_reference_id is not None
        ]
        sources = {}
        for field in values:
            all_usda = bool(ingredient_reference_ids)
            for reference_id in ingredient_reference_ids:
                reference = references_by_id.get(reference_id)
                extra = reference.get("extra_nutrients") if reference else None
                if not isinstance(extra, dict):
                    all_usda = False
                    break
                usda_extra = {
                    key: value
                    for key, value in extra.items()
                    if isinstance(value, dict) and value.get("source") == "usda_fdc"
                }
                if field not in _micros_from_reference({"extra_nutrients": usda_extra}):
                    all_usda = False
                    break
            sources[field] = "usda_fdc" if all_usda else "food_reference"
        return sources


def _micros_from_reference(reference: dict[str, Any]) -> dict[str, float]:
    micros = extra_nutrients_to_micros(
        reference.get("extra_nutrients"), validate_units=True
    )
    return micros.to_dict() if micros else {}


def _cached_values(cached: dict | None) -> dict[str, float]:
    values = cached.get("micros") if isinstance(cached, dict) else {}
    return _validated_estimate(values, MICRONUTRIENT_FIELDS)


def _has_all_micro_fields(values: dict[str, Any]) -> bool:
    return all(field in values for field in MICRONUTRIENT_FIELDS)


def _has_complete_micronutrients(meal: CatalogMeal, cached: dict | None) -> bool:
    source_values = meal.nutrition_micros.to_dict() if meal.nutrition_micros else {}
    cached_values = _cached_values(cached)
    return _has_all_micro_fields({**cached_values, **source_values})


def _validated_estimate(raw: Any, missing_fields: tuple[str, ...]) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    values: dict[str, float] = {}
    for field in missing_fields:
        value = raw.get(field)
        if isinstance(value, bool) or value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number >= 0 and math.isfinite(number):
            values[field] = number
    return values


def _with_estimate(
    meal: CatalogMeal,
    cached: dict | None,
    source_labels: dict[str, str],
    *,
    loaded: bool,
) -> CatalogMeal:
    estimated = cached.get("micros") if isinstance(cached, dict) else {}
    estimated = estimated if isinstance(estimated, dict) else {}
    cached_sources = cached.get("sources") if isinstance(cached, dict) else {}
    cached_sources = cached_sources if isinstance(cached_sources, dict) else {}
    source_values = meal.nutrition_micros.to_dict() if meal.nutrition_micros else {}
    merged = {**estimated, **source_values}
    sources = {
        str(field): str(source)
        for field, source in cached_sources.items()
        if field in merged
    }
    sources.update(source_labels)
    return replace(
        meal,
        nutrition_micros=Micros.from_dict(merged) if merged else None,
        nutrition_micros_sources=sources,
        nutrition_micros_estimated=any(
            source == "ai_estimate" for source in sources.values()
        ),
        nutrition_micros_enrichment_loaded=loaded,
    )


def build_micronutrient_estimate_prompt(
    meal: CatalogMeal,
    missing_fields: tuple[str, ...],
    known_micros: dict[str, float],
) -> str:
    """Build a USDA-grounded prompt for missing whole-recipe micros."""
    ingredients = [
        {
            "name": ingredient.display_name,
            "quantity": float(ingredient.quantity),
            "unit": ingredient.unit,
        }
        for ingredient in meal.ingredients
    ]
    servings = meal.base_servings or 1
    return (
        "Estimate total micronutrients for the entire recipe using USDA FoodData "
        "Central profiles as the reference standard. Return a non-negative numeric "
        "value for every field in the response schema; never omit a field or return "
        "null. For an unfamiliar ingredient, use the closest common USDA food "
        "profile and estimate from its quantity. Use zero only when a nutrient is "
        "negligible in one serving. Known linked-reference values are authoritative "
        "and must be repeated unchanged. Do not return macros or calories. Units: "
        "vitamin_a, vitamin_d, vitamin_k, vitamin_b12, folate, selenium in mcg; "
        "vitamin_c, vitamin_e, thiamin, riboflavin, niacin, vitamin_b6, calcium, "
        "iron, magnesium, phosphorus, potassium, sodium, zinc in mg; saturated_fat "
        "and added_sugar in g. Ingredient quantities below are for the entire recipe. "
        "Return nutrient totals for those quantities and do not divide by base servings.\n"
        f"Recipe: {meal.name}\nCuisine: {meal.cuisine}\n"
        f"Base servings: {servings}\n"
        f"Ingredients: {ingredients}\n"
        f"Known linked-reference micros: {known_micros}\n"
        f"Fields needing estimates: {list(missing_fields)}"
    )
