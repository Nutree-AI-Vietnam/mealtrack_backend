"""Source-first micronutrient enrichment for catalog recipe detail."""

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

    async def enrich(self, meal: CatalogMeal) -> CatalogMeal:
        source_values = meal.nutrition_micros.to_dict() if meal.nutrition_micros else {}
        source_labels = await self._reference_sources(meal)
        cached, claim_state, claim_token = await self._load_or_claim(
            meal, source_values
        )
        if cached is not None:
            return _with_estimate(meal, cached, source_labels, loaded=True)
        if claim_state != "claimed" or claim_token is None:
            return _with_estimate(
                meal,
                None,
                source_labels,
                loaded=claim_state == "unavailable" or claim_state == "ready",
            )

        loaded = False
        try:
            meal = await self._hydrate_linked_fdc_references(meal)
            source_values = (
                meal.nutrition_micros.to_dict() if meal.nutrition_micros else {}
            )
            source_labels = await self._reference_sources(meal)
            missing = tuple(
                field for field in MICRONUTRIENT_FIELDS if field not in source_values
            )
            estimate = {}
            if self._estimator and missing:
                raw = await self._estimator(meal, missing, source_values)
                estimate = _validated_estimate(raw, missing)
            async with self._uow_factory() as uow:
                saved = await uow.catalog_recipes.save_micronutrient_enrichment(
                    catalog_meal_id=meal.id,
                    content_hash=meal.content_hash,
                    claim_token=claim_token,
                    micros=estimate,
                    sources=dict.fromkeys(estimate, "ai_estimate"),
                )
            if saved:
                cached = {
                    "micros": estimate,
                    "sources": dict.fromkeys(estimate, "ai_estimate"),
                }
                loaded = True
        except Exception:
            logger.info(
                "catalog recipe micronutrient enrichment failed recipe_id=%s",
                meal.id,
                exc_info=True,
            )
            await self._release_failed_claim(meal, claim_token)
        return _with_estimate(meal, cached, source_labels, loaded=loaded)

    async def _hydrate_linked_fdc_references(self, meal: CatalogMeal) -> CatalogMeal:
        if self._fdc_loader is None:
            return meal
        ids = sorted(
            {
                int(item.food_reference_id)
                for item in meal.ingredients
                if item.food_reference_id is not None
            }
        )
        if not ids:
            return meal
        async with self._uow_factory() as uow:
            references = await uow.food_references.get_by_ids(ids)
        fdc_ids = sorted(
            {
                int(reference["fdc_id"])
                for reference in references
                if reference.get("fdc_id") is not None
                and len(_micros_from_reference(reference)) < len(MICRONUTRIENT_FIELDS)
            }
        )[:12]
        if not fdc_ids:
            if any(_micros_from_reference(reference) for reference in references):
                async with self._uow_factory() as uow:
                    refreshed = await uow.catalog_recipes.get_meal_detail(meal.id)
                if refreshed is not None:
                    return refreshed
            return meal
        try:
            fetched = await self._fdc_loader(fdc_ids)
        except Exception:
            logger.info("linked USDA micronutrient lookup failed", exc_info=True)
            return meal
        async with self._uow_factory() as uow:
            for reference in references:
                fdc_id = reference.get("fdc_id")
                extra = fetched.get(fdc_id)
                reference_id = reference.get("id")
                if fdc_id is not None and reference_id is not None and extra:
                    await uow.food_references.update_usda_micronutrients(
                        int(reference_id), int(fdc_id), extra
                    )
            refreshed = await uow.catalog_recipes.get_meal_detail(meal.id)
            if refreshed is not None:
                meal = refreshed
        return meal

    async def _load_or_claim(
        self, meal: CatalogMeal, source_values: dict[str, float]
    ) -> tuple[dict | None, str, str | None]:
        async with self._uow_factory() as uow:
            repository = uow.catalog_recipes
            get_cached = getattr(repository, "get_micronutrient_enrichment", None)
            cached = (
                await get_cached(
                    catalog_meal_id=meal.id,
                    content_hash=meal.content_hash,
                )
                if get_cached is not None
                else None
            )
            if (
                cached is not None
                or (self._estimator is None and self._fdc_loader is None)
                or len(source_values) == len(MICRONUTRIENT_FIELDS)
            ):
                return cached, "ready" if cached else "unavailable", None
            claim = getattr(repository, "claim_micronutrient_enrichment", None)
            if claim is None:
                return None, "unavailable", None
            state, claim_token = await claim(
                catalog_meal_id=meal.id,
                content_hash=meal.content_hash,
                lease_seconds=180,
            )
            if state == "ready" and get_cached is not None:
                cached = await get_cached(
                    catalog_meal_id=meal.id,
                    content_hash=meal.content_hash,
                )
            return cached, state, claim_token

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

    async def _release_failed_claim(self, meal: CatalogMeal, claim_token: str) -> None:
        try:
            async with self._uow_factory() as uow:
                await uow.catalog_recipes.fail_micronutrient_enrichment(
                    catalog_meal_id=meal.id,
                    content_hash=meal.content_hash,
                    claim_token=claim_token,
                    retry_seconds=300,
                )
        except Exception:
            logger.info(
                "catalog recipe micronutrient claim release failed recipe_id=%s",
                meal.id,
                exc_info=True,
            )


def _micros_from_reference(reference: dict[str, Any]) -> dict[str, float]:
    micros = extra_nutrients_to_micros(
        reference.get("extra_nutrients"), validate_units=True
    )
    return micros.to_dict() if micros else {}


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
    """Build a compact prompt for missing per-serving micronutrients only."""
    ingredients = [
        {
            "name": ingredient.display_name,
            "quantity": float(ingredient.quantity),
            "unit": ingredient.unit,
        }
        for ingredient in meal.ingredients
    ]
    return (
        "Estimate only missing micronutrients for this recipe serving. Use USDA "
        "FoodData Central nutrient profiles as the reference standard. Known "
        "values come from linked food references and must remain fixed. Do not "
        "return macros or calories. Omit unknown fields; do not use zero for "
        "unknown. Units: vitamin_a, vitamin_d, vitamin_k, vitamin_b12, folate, "
        "selenium in mcg; vitamin_c, vitamin_e, thiamin, riboflavin, niacin, "
        "vitamin_b6, calcium, iron, magnesium, phosphorus, potassium, sodium, "
        "zinc in mg; saturated_fat and added_sugar in g. Ingredient quantities "
        "shown are for one serving.\n"
        f"Recipe: {meal.name}\nCuisine: {meal.cuisine}\n"
        f"Ingredients: {ingredients}\n"
        f"Known linked-reference micros: {known_micros}\n"
        f"Fields to estimate: {list(missing_fields)}"
    )
