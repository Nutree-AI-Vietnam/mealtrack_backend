"""Recipe list/detail projections backed by the curated catalog."""

from __future__ import annotations

import asyncio
import json
import logging
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
            if self.redis_client is not None:
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
