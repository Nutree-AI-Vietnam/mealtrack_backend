"""Source-first micronutrient enrichment for catalog recipes."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace
from typing import Any

from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.nutrition.extra_nutrients import extra_nutrients_to_micros
from src.domain.model.nutrition.micros import Micros

logger = logging.getLogger(__name__)
_PENDING_ENRICHMENT_WAIT_SECONDS = 90.0
_PENDING_ENRICHMENT_POLL_SECONDS = 0.5
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

    async def enrich(
        self,
        meal: CatalogMeal,
        *,
        provider_semaphore: asyncio.Semaphore | None = None,
        pending_deadline: float | None = None,
    ) -> CatalogMeal:
        source_values = meal.nutrition_micros.to_dict() if meal.nutrition_micros else {}
        source_labels = await self._reference_sources(meal)
        cached, claim_state, claim_token = await self._load_or_claim(
            meal, source_values
        )
        if cached is not None and claim_state == "ready":
            loaded = _has_complete_micronutrients(meal, cached)
            return _with_estimate(meal, cached, source_labels, loaded=loaded)
        if claim_state == "pending":
            cached = await self._wait_for_cached_enrichment(
                meal, deadline=pending_deadline
            )
            return _with_estimate(
                meal,
                cached,
                source_labels,
                loaded=_has_complete_micronutrients(meal, cached),
            )
        if claim_state != "claimed" or claim_token is None:
            return _with_estimate(
                meal,
                None,
                source_labels,
                loaded=claim_state in {"complete", "ready"},
            )

        loaded = False
        try:
            meal = await self._hydrate_linked_fdc_references(
                meal, provider_semaphore=provider_semaphore
            )
            source_values = (
                meal.nutrition_micros.to_dict() if meal.nutrition_micros else {}
            )
            source_labels = await self._reference_sources(meal)
            cached_values = _cached_values(cached)
            known_values = {**cached_values, **source_values}
            missing = tuple(
                field for field in MICRONUTRIENT_FIELDS if field not in known_values
            )
            estimate = {}
            if self._estimator and missing:
                if provider_semaphore is None:
                    raw = await self._estimator(meal, missing, known_values)
                else:
                    async with provider_semaphore:
                        raw = await self._estimator(meal, missing, known_values)
                estimate = _validated_estimate(raw, missing)
            cached_micros = {**cached_values, **estimate, **source_values}
            if not _has_all_micro_fields(cached_micros):
                logger.warning(
                    "catalog recipe micronutrient estimate incomplete recipe_id=%s "
                    "missing_fields=%s",
                    meal.id,
                    sorted(set(MICRONUTRIENT_FIELDS) - set(cached_micros)),
                )
                await self._release_failed_claim(meal, claim_token)
                return _with_estimate(meal, cached, source_labels, loaded=False)
            cached_sources = dict(cached.get("sources", {})) if cached else {}
            cached_sources.update(dict.fromkeys(estimate, "ai_estimate"))
            cached_sources.update(source_labels)
            async with self._uow_factory() as uow:
                saved = await uow.catalog_recipes.save_micronutrient_enrichment(
                    catalog_meal_id=meal.id,
                    content_hash=meal.content_hash,
                    claim_token=claim_token,
                    micros=cached_micros,
                    sources=cached_sources,
                )
            if saved:
                cached = {
                    "micros": cached_micros,
                    "sources": cached_sources,
                }
            else:
                await self._release_failed_claim(meal, claim_token)
            loaded = saved and _has_complete_micronutrients(meal, cached)
        except Exception:
            logger.info(
                "catalog recipe micronutrient enrichment failed recipe_id=%s",
                meal.id,
                exc_info=True,
            )
            await self._release_failed_claim(meal, claim_token)
        return _with_estimate(meal, cached, source_labels, loaded=loaded)

    async def enrich_recipe_ids(self, recipe_ids: Iterable[str]) -> bool:
        """Enrich every recipe used by a saved plan with bounded concurrency."""
        ids = sorted({str(recipe_id) for recipe_id in recipe_ids if recipe_id})
        if not ids:
            return True
        try:
            async with self._uow_factory() as uow:
                meals = await uow.catalog_recipes.get_meals(ids)
        except Exception:
            logger.info(
                "weekly-plan micronutrient recipes could not be loaded", exc_info=True
            )
            return False
        # Plans can outlive a catalog release. Only currently active recipes
        # participate in first-open readiness.
        if not meals:
            return True

        provider_semaphore = asyncio.Semaphore(4)
        loop = asyncio.get_running_loop()
        pending_deadline = loop.time() + _PENDING_ENRICHMENT_WAIT_SECONDS

        async def enrich_one(meal: CatalogMeal) -> bool:
            try:
                result = await self.enrich(
                    meal,
                    provider_semaphore=provider_semaphore,
                    pending_deadline=pending_deadline,
                )
                return result.nutrition_micros_enrichment_loaded
            except Exception:
                logger.info(
                    "weekly-plan micronutrient enrichment failed recipe_id=%s",
                    meal.id,
                    exc_info=True,
                )
                return False

        readiness = await asyncio.gather(*(enrich_one(meal) for meal in meals))
        return all(readiness)

    async def load_cached(self, meal: CatalogMeal) -> CatalogMeal:
        """Overlay persisted estimates without claiming work or calling providers."""
        async with self._uow_factory() as uow:
            cached = await uow.catalog_recipes.get_micronutrient_enrichment(
                catalog_meal_id=meal.id,
                content_hash=meal.content_hash,
            )
        source_labels = await self._reference_sources(meal)
        loaded = _has_complete_micronutrients(meal, cached)
        return _with_estimate(meal, cached, source_labels, loaded=loaded)

    async def _wait_for_cached_enrichment(
        self, meal: CatalogMeal, *, deadline: float | None = None
    ) -> dict | None:
        loop = asyncio.get_running_loop()
        deadline = deadline or loop.time() + _PENDING_ENRICHMENT_WAIT_SECONDS
        while loop.time() < deadline:
            await asyncio.sleep(_PENDING_ENRICHMENT_POLL_SECONDS)
            try:
                async with self._uow_factory() as uow:
                    repository = uow.catalog_recipes
                    cached = await repository.get_micronutrient_enrichment(
                        catalog_meal_id=meal.id,
                        content_hash=meal.content_hash,
                    )
                    get_status = getattr(
                        repository, "get_micronutrient_enrichment_status", None
                    )
                    state = (
                        await get_status(
                            catalog_meal_id=meal.id,
                            content_hash=meal.content_hash,
                        )
                        if get_status is not None
                        else "pending"
                    )
            except Exception:
                logger.info(
                    "pending micronutrient enrichment check failed recipe_id=%s",
                    meal.id,
                    exc_info=True,
                )
                continue
            if cached is not None and _has_complete_micronutrients(meal, cached):
                return cached
            if state == "ready":
                continue
            if state != "pending":
                return None
        logger.info(
            "pending micronutrient enrichment did not finish before timeout recipe_id=%s",
            meal.id,
        )
        return None

    async def _hydrate_linked_fdc_references(
        self,
        meal: CatalogMeal,
        *,
        provider_semaphore: asyncio.Semaphore | None = None,
    ) -> CatalogMeal:
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
            if provider_semaphore is None:
                fetched = await self._fdc_loader(fdc_ids)
            else:
                async with provider_semaphore:
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
            if len(source_values) == len(MICRONUTRIENT_FIELDS):
                return None, "complete", None
            if cached is not None and _has_complete_micronutrients(meal, cached):
                return cached, "ready", None
            if self._estimator is None and self._fdc_loader is None:
                return None, "unavailable", None
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
                if not _has_complete_micronutrients(meal, cached):
                    return cached, "unavailable", None
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
