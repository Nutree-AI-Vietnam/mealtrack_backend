"""
Three-tier nutrition lookup orchestration service.

Tier resolution order per ingredient:
  Redis — cached per-100g macros (24-hour TTL)
  T1 — exact match on food_reference.name_normalized (MySQL, no fuzzy)
  T2 — fatsecret via IngredientNutritionResolver (caches result to T1)
  T3 — AI single-ingredient estimate (last resort, logged as WARNING)

Calories are ALWAYS derived: P×4 + (C−fiber)×4 + fiber×2 + F×9
"""

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from src.domain.constants.food_density import get_density
from src.domain.model.nutrition.macros import Macros
from src.domain.ports.cache_port import CachePort
from src.domain.services.meal_suggestion.ingredient_name_normalizer import (
    normalize_food_name,
)

logger = logging.getLogger(__name__)

# Cache metrics for observability
_cache_metrics = {
    "redis_hits": 0,
    "redis_misses": 0,
    "t1_hits": 0,
    "t2_hits": 0,
    "t3_hits": 0,
}

NUTRITION_CACHE_TTL = 86400  # 24 hours
T2_TIMEOUT = 2.0  # fatsecret timeout
T3_TIMEOUT = 3.0  # AI estimate timeout

# Volume conversions: unit → millilitres
_VOLUME_TO_ML: dict[str, float] = {"cup": 240.0, "tbsp": 15.0, "tsp": 5.0}


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass
class IngredientMacros:
    """Calculated macros for a specific ingredient quantity."""

    name: str
    quantity_g: float
    calories: float  # derived: P×4 + (C−fiber)×4 + fiber×2 + F×9
    protein: float
    carbs: float
    fat: float
    fiber: float
    sugar: float
    source_tier: str  # "T1_food_reference" | "T2_fatsecret" | "T3_ai_estimate"
    food_reference_id: int | None = field(default=None)


@dataclass
class MealMacros:
    """Aggregated macros for an entire meal."""

    calories: float
    protein: float
    carbs: float
    fat: float
    fiber: float
    sugar: float
    ingredients: list[IngredientMacros]
    t1_count: int  # resolved via food_reference
    t2_count: int  # resolved via fatsecret
    t3_count: int  # resolved via AI fallback


# ---------------------------------------------------------------------------
# Pydantic schema for T3 AI structured output
# ---------------------------------------------------------------------------


class SingleIngredientSchema(BaseModel):
    """Per-100g macros returned by the AI for a single ingredient."""

    protein: float = Field(..., ge=0)
    carbs: float = Field(..., ge=0)
    fat: float = Field(..., ge=0)
    fiber: float = Field(default=0.0, ge=0)
    sugar: float = Field(default=0.0, ge=0)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class NutritionLookupService:
    """Resolve macros for meal ingredients using three-tier lookup."""

    def __init__(
        self,
        food_ref_repo: Any,
        ingredient_nutrition_resolver: Any,
        generation_service: Any,
        redis_client: Any = None,
        cache_service: CachePort | None = None,
    ) -> None:
        self._repo = food_ref_repo
        self._resolver = ingredient_nutrition_resolver
        self._gen = generation_service
        self._redis = redis_client
        self._cache_service = cache_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def calculate_meal_macros(
        self, ingredients: list[dict[str, Any]]
    ) -> MealMacros:
        """Calculate deterministic macros for a list of ingredients.

        Args:
            ingredients: [{"name": str, "amount": float, "unit": str}, ...]

        Returns:
            MealMacros with aggregated totals and per-ingredient breakdown.
        """
        # Normalize names and convert to grams once per ingredient.
        prepared = [
            (
                ing["name"],
                normalize_food_name(ing["name"]),
                self._to_grams(ing["name"], float(ing["amount"]), ing["unit"]),
            )
            for ing in ingredients
        ]

        # Tier 0 (Redis): check every key concurrently.
        results: list[IngredientMacros | None] = list(
            await asyncio.gather(
                *(
                    self._check_redis_cache(normalized, name, quantity_g)
                    for name, normalized, quantity_g in prepared
                )
            )
        )

        # Tier 1 (food_reference): resolve every Redis miss with ONE batched
        # query instead of N concurrent single-row lookups.
        miss_normalized = [
            normalized
            for (name, normalized, quantity_g), cached in zip(
                prepared, results, strict=False
            )
            if cached is None
        ]
        t1_map: dict[str, dict[str, Any]] = {}
        if miss_normalized:
            t1_map = await self._find_batch_by_normalized_names(miss_normalized)

        # Resolve the Redis misses concurrently: T1 from the batch map → T2 → T3.
        pending = [i for i, cached in enumerate(results) if cached is None]

        async def _resolve(index: int) -> IngredientMacros:
            name, normalized, quantity_g = prepared[index]
            _cache_metrics["redis_misses"] += 1
            return await self._resolve_uncached(
                name, normalized, quantity_g, t1_map.get(normalized)
            )

        resolved = await asyncio.gather(*(_resolve(i) for i in pending))
        for index, macros in zip(pending, resolved, strict=False):
            results[index] = macros

        meal = self._aggregate([m for m in results if m is not None])
        total = _cache_metrics["redis_hits"] + _cache_metrics["redis_misses"]
        if total > 0 and total % 100 == 0:
            self.log_cache_metrics()
        return meal

    # ------------------------------------------------------------------
    # Three-tier lookup
    # ------------------------------------------------------------------

    async def _check_redis_cache(
        self, normalized: str, name: str, quantity_g: float
    ) -> IngredientMacros | None:
        """Tier 0: return cached macros from Redis, or None on miss/disabled/error.

        Increments the redis_hits metric on a hit; misses are counted by the
        caller so the totals are identical whether lookups run singly (via
        _lookup_ingredient) or batched (via calculate_meal_macros).
        """
        if not self._redis:
            return None
        cache_key = f"nutrition:{normalized}"
        try:
            cached = await self._redis.get(cache_key)
            if cached:
                data = json.loads(cached)
                _cache_metrics["redis_hits"] += 1
                return self._build_from_cached(data, name, quantity_g)
        except Exception as exc:
            logger.warning("Redis get failed for %s: %s", cache_key, exc)
        return None

    async def _resolve_uncached(
        self,
        name: str,
        normalized: str,
        quantity_g: float,
        t1_ref: dict[str, Any] | None,
    ) -> IngredientMacros:
        """Resolve a Redis-missed ingredient: T1 (caller-supplied ref) → T2 → T3.

        ``t1_ref`` is the food_reference row for ``normalized`` (or None), fetched
        by the caller either singly or in one batch. The resolved per-100g macros
        are cached back to Redis.
        """
        cache_key = f"nutrition:{normalized}"

        # T1: exact match on name_normalized (already fetched by the caller).
        if t1_ref:
            _cache_metrics["t1_hits"] += 1
            result = self._calculate_from_ref(
                t1_ref, name, quantity_g, "T1_food_reference"
            )
            await self._cache_result(cache_key, result)
            return result

        # T2: fatsecret (resolver handles caching to food_reference)
        try:
            per100 = await asyncio.wait_for(
                self._resolver.resolve(name), timeout=T2_TIMEOUT
            )
        except TimeoutError:
            logger.warning("T2 fatsecret timeout for %s", name)
            per100 = None
        if per100 is not None:
            _cache_metrics["t2_hits"] += 1
            result = self._build_from_per100(per100, name, quantity_g, "T2_fatsecret")
            await self._cache_result(cache_key, result)
            return result

        # T3: AI estimate — last resort
        _cache_metrics["t3_hits"] += 1
        try:
            result = await asyncio.wait_for(
                self._ai_estimate(name, quantity_g), timeout=T3_TIMEOUT
            )
            await self._cache_result(cache_key, result)
            return result
        except TimeoutError:
            logger.warning("T3 AI timeout for %s", name)
            return IngredientMacros(
                name=name,
                quantity_g=round(quantity_g, 1),
                calories=0.0,
                protein=0.0,
                carbs=0.0,
                fat=0.0,
                fiber=0.0,
                sugar=0.0,
                source_tier="T3_ai_estimate",
            )

    async def _lookup_ingredient(
        self, name: str, quantity_g: float
    ) -> IngredientMacros:
        """Resolve macros for one ingredient: Redis → T1 → T2 → T3.

        Standalone per-ingredient path. calculate_meal_macros batches the T1
        tier across all ingredients rather than calling this in a loop, but the
        resolution semantics are identical.
        """
        normalized = normalize_food_name(name)
        cached = await self._check_redis_cache(normalized, name, quantity_g)
        if cached is not None:
            return cached
        _cache_metrics["redis_misses"] += 1
        ref = await self._find_by_normalized_name(normalized)
        return await self._resolve_uncached(name, normalized, quantity_g, ref)

    async def _find_batch_by_normalized_names(
        self, names_normalized: list[str]
    ) -> dict[str, dict[str, Any]]:
        method = self._repo.find_batch_by_normalized_names
        if inspect.iscoroutinefunction(method):
            return await method(names_normalized)
        return await asyncio.to_thread(method, names_normalized)

    async def _find_by_normalized_name(
        self, name_normalized: str
    ) -> dict[str, Any] | None:
        method = self._repo.find_by_normalized_name
        if inspect.iscoroutinefunction(method):
            return await method(name_normalized)
        return await asyncio.to_thread(method, name_normalized)

    # ------------------------------------------------------------------
    # Redis cache helpers
    # ------------------------------------------------------------------

    def _build_from_cached(
        self, data: dict[str, Any], name: str, quantity_g: float
    ) -> IngredientMacros:
        """Build IngredientMacros from cached per-100g data."""
        factor = quantity_g / 100.0
        protein = data["protein"] * factor
        carbs = data["carbs"] * factor
        fat = data["fat"] * factor
        fiber = data.get("fiber", 0.0) * factor
        sugar = data.get("sugar", 0.0) * factor
        calories = _derive_calories(protein, carbs, fat, fiber)
        return IngredientMacros(
            name=name,
            quantity_g=round(quantity_g, 1),
            calories=round(calories, 1),
            protein=round(protein, 1),
            carbs=round(carbs, 1),
            fat=round(fat, 1),
            fiber=round(fiber, 1),
            sugar=round(sugar, 1),
            source_tier=data.get("source_tier", "cached"),
            food_reference_id=data.get("food_reference_id"),
        )

    async def _cache_result(self, key: str, result: IngredientMacros) -> None:
        """Schedule per-100g macros cache population."""
        if self._cache_service is None:
            return
        try:
            factor = 100.0 / result.quantity_g if result.quantity_g > 0 else 1.0
            data = {
                "protein": round(result.protein * factor, 2),
                "carbs": round(result.carbs * factor, 2),
                "fat": round(result.fat * factor, 2),
                "fiber": round(result.fiber * factor, 2),
                "sugar": round(result.sugar * factor, 2),
                "source_tier": result.source_tier,
            }
            if result.food_reference_id is not None:
                data["food_reference_id"] = result.food_reference_id
            await self._cache_service.set(key, data, NUTRITION_CACHE_TTL)
        except Exception as exc:
            logger.warning("Redis set failed for %s: %s", key, exc)

    # ------------------------------------------------------------------
    # Calculation helpers
    # ------------------------------------------------------------------

    def _calculate_from_ref(
        self,
        ref: dict[str, Any],
        name: str,
        quantity_g: float,
        tier: str,
    ) -> IngredientMacros:
        """Scale per-100g values from a food_reference dict to quantity_g."""
        factor = quantity_g / 100.0
        protein = (ref.get("protein_100g") or 0.0) * factor
        carbs = (ref.get("carbs_100g") or 0.0) * factor
        fat = (ref.get("fat_100g") or 0.0) * factor
        fiber = (ref.get("fiber_100g") or 0.0) * factor
        sugar = (ref.get("sugar_100g") or 0.0) * factor
        calories = _derive_calories(protein, carbs, fat, fiber)
        return IngredientMacros(
            name=name,
            quantity_g=round(quantity_g, 1),
            calories=round(calories, 1),
            protein=round(protein, 1),
            carbs=round(carbs, 1),
            fat=round(fat, 1),
            fiber=round(fiber, 1),
            sugar=round(sugar, 1),
            source_tier=tier,
            food_reference_id=ref.get("id"),
        )

    def _build_from_per100(
        self,
        per100: Any,  # PerHundredGramsMacros from ingredient_nutrition_resolver
        name: str,
        quantity_g: float,
        tier: str,
    ) -> IngredientMacros:
        """Build IngredientMacros from a PerHundredGramsMacros dataclass."""
        factor = quantity_g / 100.0
        protein = per100.protein * factor
        carbs = per100.carbs * factor
        fat = per100.fat * factor
        fiber = per100.fiber * factor
        sugar = per100.sugar * factor
        calories = _derive_calories(protein, carbs, fat, fiber)
        return IngredientMacros(
            name=name,
            quantity_g=round(quantity_g, 1),
            calories=round(calories, 1),
            protein=round(protein, 1),
            carbs=round(carbs, 1),
            fat=round(fat, 1),
            fiber=round(fiber, 1),
            sugar=round(sugar, 1),
            source_tier=tier,
            food_reference_id=None,
        )

    async def _ai_estimate(self, name: str, quantity_g: float) -> IngredientMacros:
        """T3 fallback: ask AI for per-100g macros. Never raises."""
        logger.warning("T3 AI estimate used for ingredient: %s", name)

        prompt = (
            f"What are the macros per 100g for '{name}'? "
            "Return JSON with keys: protein, carbs, fat, fiber, sugar (all floats, grams)."
        )
        system_message = (
            "You are a nutrition database assistant. "
            "Return only JSON with per-100g macros."
        )

        try:
            raw = await asyncio.wait_for(
                self._gen.generate_meal_plan_async(
                    prompt,
                    system_message,
                    "json",
                    256,
                    SingleIngredientSchema,
                    None,
                ),
                timeout=10.0,
            )
            # generate_meal_plan returns a dict when schema is provided
            schema_data = (
                SingleIngredientSchema(**raw) if isinstance(raw, dict) else raw
            )
            return self._build_from_per100(
                schema_data, name, quantity_g, "T3_ai_estimate"
            )
        except Exception as exc:
            logger.error("T3 AI estimate failed for ingredient '%s': %s", name, exc)
            return IngredientMacros(
                name=name,
                quantity_g=round(quantity_g, 1),
                calories=0.0,
                protein=0.0,
                carbs=0.0,
                fat=0.0,
                fiber=0.0,
                sugar=0.0,
                source_tier="T3_ai_estimate",
            )

    # ------------------------------------------------------------------
    # Unit conversion
    # ------------------------------------------------------------------

    def _to_grams(self, name: str, amount: float, unit: str) -> float:
        """Convert amount+unit to grams using food density when needed."""
        unit_lower = unit.lower().strip()

        if unit_lower == "g":
            return amount

        if unit_lower == "ml":
            return amount * get_density(name)

        if unit_lower in _VOLUME_TO_ML:
            ml = amount * _VOLUME_TO_ML[unit_lower]
            return ml * get_density(name)

        # Unknown unit — assume grams
        logger.warning(
            "Unknown unit '%s' for ingredient '%s'; assuming grams", unit, name
        )
        return amount

    # ------------------------------------------------------------------
    # Calorie scaling
    # ------------------------------------------------------------------

    def scale_to_target(
        self,
        meal_macros: MealMacros,
        target_calories: int,
        reject_out_of_range: bool = True,
    ) -> MealMacros | None:
        """Scale ingredient quantities so total calories ≈ target_calories.

        Returns scaled MealMacros, or None if scale factor is outside 0.7–1.4
        (caller should regenerate the recipe), unless reject_out_of_range=False.

        Edge cases:
          - meal_macros.calories <= 0: return None — zero-cal recipes cannot be scaled
            and must not be served (caller should reject the recipe).
          - target_calories <= 0: return meal_macros unchanged, log warning
            (programming bug; different from a zero-cal ingredient result).
        """
        if target_calories <= 0:
            logger.warning(
                "scale_to_target: target_calories=%d is invalid, returning macros unchanged",
                target_calories,
            )
            return meal_macros

        if meal_macros.calories <= 0:
            logger.warning(
                "scale_to_target: meal has 0 kcal (all T3 lookups failed?) — rejecting recipe"
            )
            return None

        scale = target_calories / meal_macros.calories

        if reject_out_of_range and (scale < 0.7 or scale > 1.4):
            logger.warning(
                "scale_to_target: scale factor %.2f out of range [0.7, 1.4] "
                "(actual=%.1f kcal, target=%d kcal) — recipe rejected",
                scale,
                meal_macros.calories,
                target_calories,
            )
            return None

        scaled_ingredients = [
            IngredientMacros(
                name=ing.name,
                quantity_g=round(ing.quantity_g * scale, 1),
                calories=0.0,  # re-derived by _aggregate
                protein=round(ing.protein * scale, 1),
                carbs=round(ing.carbs * scale, 1),
                fat=round(ing.fat * scale, 1),
                fiber=round(ing.fiber * scale, 1),
                sugar=round(ing.sugar * scale, 1),
                source_tier=ing.source_tier,
                food_reference_id=ing.food_reference_id,
            )
            for ing in meal_macros.ingredients
        ]
        return self._aggregate(scaled_ingredients)

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def _aggregate(self, ingredients: list[IngredientMacros]) -> MealMacros:
        """Sum ingredient macros and count tier hits."""
        total_protein = sum(i.protein for i in ingredients)
        total_carbs = sum(i.carbs for i in ingredients)
        total_fat = sum(i.fat for i in ingredients)
        total_fiber = sum(i.fiber for i in ingredients)
        total_sugar = sum(i.sugar for i in ingredients)
        total_calories = _derive_calories(
            total_protein, total_carbs, total_fat, total_fiber
        )

        return MealMacros(
            calories=round(total_calories, 1),
            protein=round(total_protein, 1),
            carbs=round(total_carbs, 1),
            fat=round(total_fat, 1),
            fiber=round(total_fiber, 1),
            sugar=round(total_sugar, 1),
            ingredients=ingredients,
            t1_count=sum(
                1 for i in ingredients if i.source_tier == "T1_food_reference"
            ),
            t2_count=sum(1 for i in ingredients if i.source_tier == "T2_fatsecret"),
            t3_count=sum(1 for i in ingredients if i.source_tier == "T3_ai_estimate"),
        )

    @staticmethod
    def get_cache_metrics() -> dict:
        """Return current cache metrics for monitoring."""
        total = _cache_metrics["redis_hits"] + _cache_metrics["redis_misses"]
        hit_rate = _cache_metrics["redis_hits"] / total * 100 if total > 0 else 0.0
        return {
            **_cache_metrics,
            "total_lookups": total,
            "redis_hit_rate_pct": round(hit_rate, 1),
        }

    @staticmethod
    def log_cache_metrics() -> None:
        """Log current cache metrics at INFO level."""
        metrics = NutritionLookupService.get_cache_metrics()
        logger.info(
            "[NUTRITION-CACHE] hits=%d misses=%d hit_rate=%.1f%% | "
            "T1=%d T2=%d T3=%d",
            metrics["redis_hits"],
            metrics["redis_misses"],
            metrics["redis_hit_rate_pct"],
            metrics["t1_hits"],
            metrics["t2_hits"],
            metrics["t3_hits"],
        )


# ---------------------------------------------------------------------------
# Module-level utility
# ---------------------------------------------------------------------------


def _derive_calories(protein: float, carbs: float, fat: float, fiber: float) -> float:
    """Fiber-aware calorie derivation: P×4 + (C−fiber)×4 + fiber×2 + F×9."""
    return Macros.raw_total_calories(protein, carbs, fat, fiber)
