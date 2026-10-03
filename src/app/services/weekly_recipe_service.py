"""Recipe list/detail projections backed by the curated catalog."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol

from src.app.services.catalog_recipe_micronutrient_enrichment_service import (
    CatalogRecipeMicronutrientEnrichmentService,
    FdcMicronutrientLoader,
    MicronutrientEstimator,
)
from src.domain.cache.cache_keys import CacheKeys
from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.services.weekly_meal_planner.allergen_constraint import (
    recipe_excluded_by_allergen,
    resolve_allergen_preferences,
)
from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
    is_non_meal_title,
)
from src.planner_observability import planner_phase, planner_timed

logger = logging.getLogger(__name__)


class RecipeCachePort(Protocol):
    """Protocol for multi-key recipe cache operations."""

    async def mget(self, keys: list[str]) -> list[str | None]: ...
    async def mset_with_ttl(self, mapping: dict[str, str], ttl: int) -> bool: ...


@dataclass(frozen=True)
class RecipePage:
    items: tuple[CatalogMeal, ...]
    total: int


class WeeklyRecipeService:
    """Apply bounded, case-insensitive read filters to catalog projections."""

    def __init__(
        self,
        uow_factory: Any,
        *,
        micronutrient_enrichment: CatalogRecipeMicronutrientEnrichmentService
        | None = None,
        micronutrient_estimator: MicronutrientEstimator | None = None,
        fdc_micronutrient_loader: FdcMicronutrientLoader | None = None,
        redis_client: RecipeCachePort | None = None,
    ):
        self.uow_factory = uow_factory
        self.redis_client = redis_client
        self.micronutrient_enrichment = (
            micronutrient_enrichment
            or CatalogRecipeMicronutrientEnrichmentService(
                uow_factory,
                estimator=micronutrient_estimator,
                fdc_loader=fdc_micronutrient_loader,
            )
        )

    @planner_timed("catalog_sql")
    async def list(
        self,
        *,
        query=None,
        diet=None,
        max_cook_time=None,
        cuisine=None,
        meal_type=None,
        dislikes=(),
        allergies=(),
        limit=20,
        offset=0,
    ) -> RecipePage:
        async with self.uow_factory() as uow:
            if os.getenv("CATALOG_PROJECTIONS_ENABLED", "false").lower() in {
                "true",
                "1",
            }:
                page = await uow.catalog_recipes.list_recipe_page(
                    query=query,
                    diet=diet,
                    max_cook_time=max_cook_time,
                    cuisine=cuisine,
                    meal_type=meal_type,
                    dislikes=dislikes,
                    allergies=allergies,
                    limit=limit,
                    offset=offset,
                )
                return RecipePage(items=page.items, total=page.total)
            meals = await uow.catalog_recipes.list_active_meals(
                cuisine=cuisine,
                meal_type=meal_type,
            )
            known_allergen_codes = (
                await uow.catalog_recipes.list_allergen_codes() if allergies else ()
            )
        allergen_codes = resolve_allergen_preferences(
            allergies,
            known_allergen_codes,
        )
        if allergen_codes is None:
            return RecipePage(items=(), total=0)
        filtered = [
            meal
            for meal in meals
            if _matches_query(meal, query)
            and _matches_diet(meal, diet)
            and _matches_time(meal, max_cook_time)
            and not _contains_terms(meal, dislikes)
            and not (
                meal_type in {"lunch", "dinner"}
                and is_non_meal_title(meal.name, meal.tag)
            )
            and not recipe_excluded_by_allergen(meal.allergen_codes, allergen_codes)
        ]
        ordered = sorted(
            filtered,
            key=lambda item: (
                item.popularity_rank is None,
                item.popularity_rank or 0,
                item.name.casefold(),
                item.id,
            ),
        )
        return RecipePage(
            items=tuple(ordered[offset : offset + limit]), total=len(ordered)
        )

    @planner_timed("catalog_sql")
    async def detail(
        self, recipe_id: str, *, include_cached_micronutrients: bool = True
    ) -> CatalogMeal | None:
        async with self.uow_factory() as uow:
            meal = await uow.catalog_recipes.get_meal_detail(recipe_id)
            if meal is None:
                return None
        if not include_cached_micronutrients:
            return meal
        return await self.micronutrient_enrichment.load_cached(meal)

    @planner_timed("catalog_sql")
    async def summaries(self, recipe_ids: Iterable[str]) -> tuple[CatalogMeal, ...]:
        ids = tuple(dict.fromkeys(recipe_id for recipe_id in recipe_ids if recipe_id))
        if not ids:
            return ()

        # Optional Redis has one total budget; a slow read suppresses its write.
        cache_deadline = monotonic() + 0.1
        cache_healthy = self.redis_client is not None
        cached_meals: dict[str, CatalogMeal] = {}
        missing_ids = list(ids)
        version = None
        async with self.uow_factory() as uow:
            if self.redis_client is not None and os.getenv(
                "CATALOG_PROJECTIONS_ENABLED", "false"
            ).lower() in {"true", "1"}:
                version = await uow.catalog_recipes.lock_catalog_publication(
                    shared=True
                )
            keys = [f"catalog:summary:v2:{version}:{mid}" for mid in ids]
            # Unversioned legacy cache rows cannot safely survive withdrawals.
            if cache_healthy and version is not None and self.redis_client is not None:
                try:
                    async with asyncio.timeout(
                        max(0.001, cache_deadline - monotonic())
                    ):
                        with planner_phase("redis"):
                            cached_raw = await self.redis_client.mget(keys)
                    for mid, raw in zip(ids, cached_raw, strict=False):
                        if raw:
                            try:
                                cached_meals[mid] = CatalogMeal.from_dict(
                                    json.loads(raw)
                                )
                            except (ValueError, TypeError, KeyError):
                                pass
                    missing_ids = [mid for mid in ids if mid not in cached_meals]
                except Exception:
                    cache_healthy = False
            if missing_ids:
                loader = getattr(uow.catalog_recipes, "get_meal_summaries", None)
                fetched = await (
                    loader(missing_ids)
                    if callable(loader)
                    else uow.catalog_recipes.get_meals(missing_ids)
                )
                for meal in fetched:
                    cached_meals[meal.id] = meal
                remaining = cache_deadline - monotonic()
                if (
                    cache_healthy
                    and version is not None
                    and remaining > 0
                    and self.redis_client is not None
                ):
                    mapping = {
                        f"catalog:summary:v2:{version}:{meal.id}": json.dumps(
                            meal.to_dict()
                        )
                        for meal in fetched
                    }
                    if mapping:
                        try:
                            async with asyncio.timeout(remaining):
                                with planner_phase("redis"):
                                    await self.redis_client.mset_with_ttl(
                                        mapping, CacheKeys.TTL_7_DAYS
                                    )
                        except Exception:
                            pass
        return tuple(cached_meals[mid] for mid in ids if mid in cached_meals)


def _matches_query(meal: CatalogMeal, query: str | None) -> bool:
    needle = (query or "").strip().casefold()
    if not needle:
        return True
    haystack = " ".join(
        [
            meal.name,
            meal.cuisine,
            meal.description or "",
            *(item.name for item in meal.ingredients),
        ]
    ).casefold()
    return needle in haystack


def _matches_diet(meal: CatalogMeal, diet: str | None) -> bool:
    if not diet or diet == "any":
        return True
    haystack = " ".join(
        [meal.name, meal.description or "", *(item.name for item in meal.ingredients)]
    ).casefold()
    if diet == "vegetarian":
        return not any(
            term in haystack
            for term in (
                "beef",
                "chicken",
                "pork",
                "fish",
                "shrimp",
                "meat",
                "thịt",
                "gà",
                "bò",
                "cá",
            )
        )
    if diet == "no-pork":
        return not any(
            term in haystack
            for term in ("pork", "ham", "bacon", "sausage", "thịt heo", "thịt lợn")
        )
    return False


def _matches_time(meal: CatalogMeal, max_cook_time: int | None) -> bool:
    if max_cook_time is None:
        return True
    total = int(meal.prep_time_minutes or 0) + int(meal.cook_time_minutes or 0)
    return total <= max_cook_time


def _contains_terms(meal: CatalogMeal, terms: tuple[str, ...]) -> bool:
    if not terms:
        return False
    haystack = " ".join(
        [meal.name, meal.description or "", *(item.name for item in meal.ingredients)]
    ).casefold()
    return any(term.strip().casefold() in haystack for term in terms if term.strip())
