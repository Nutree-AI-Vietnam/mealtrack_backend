"""Offline benchmark for cross-recipe nutrition lookup deduplication.

Run from the backend root with:
    PYTHONPATH=. .venv/bin/python plans/2026-10-28-ai-reliability-accuracy-speed/benchmark_lookup_dedup.py

The fake provider has a fixed 20 ms response and a shared four-call limit. It
measures lookup orchestration only; it does not represent production provider
latency, database latency, model quality, or end-to-end recipe generation.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from src.domain.services.meal_suggestion.ingredient_name_normalizer import (
    normalize_food_identity,
)
from src.domain.services.meal_suggestion.ingredient_nutrition_resolver import (
    PerHundredGramsMacros,
)
from src.domain.services.meal_suggestion.nutrition_lookup_service import (
    NUTRITION_CACHE_PREFIX,
    NutritionLookupService,
)

SAMPLE_COUNT = 30
PROVIDER_LATENCY_SECONDS = 0.020
PROVIDER_CONCURRENCY = 4
RECIPE_COUNT = 3
INGREDIENTS_PER_RECIPE = 4
MACROS = {
    "protein": 10.0,
    "carbs": 10.0,
    "fat": 5.0,
    "fiber": 1.0,
    "sugar": 1.0,
}
CACHE_MACROS = {**MACROS, "source_tier": "T2_fatsecret"}


@dataclass
class _RunState:
    cache: dict[str, str] = field(default_factory=dict)
    provider_calls: int = 0
    batch_queries: int = 0
    provider_gate: asyncio.Semaphore | None = None


class _Redis:
    def __init__(self, state: _RunState) -> None:
        self.state = state

    async def get(self, key: str) -> str | None:
        return self.state.cache.get(key)


class _CacheWriter:
    def __init__(self, state: _RunState) -> None:
        self.state = state

    async def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        del ttl
        self.state.cache[key] = json.dumps(value)


class _FoodReferences:
    def __init__(self, state: _RunState) -> None:
        self.state = state

    async def find_batch_by_normalized_names(
        self, names: list[str]
    ) -> dict[str, dict[str, Any]]:
        del names
        self.state.batch_queries += 1
        return {}


class _Provider:
    def __init__(self, state: _RunState) -> None:
        self.state = state

    async def resolve(
        self, name: str, *, name_normalized: str
    ) -> PerHundredGramsMacros:
        del name, name_normalized
        self.state.provider_calls += 1
        assert self.state.provider_gate is not None
        async with self.state.provider_gate:
            await asyncio.sleep(PROVIDER_LATENCY_SECONDS)
        return PerHundredGramsMacros(**MACROS)


def _recipe_ingredients(workload: str) -> list[list[dict[str, Any]]]:
    meals: list[list[dict[str, Any]]] = []
    for meal_index in range(RECIPE_COUNT):
        ingredients = []
        for ingredient_index in range(INGREDIENTS_PER_RECIPE):
            name = (
                f"food {ingredient_index}"
                if workload == "repeated"
                else f"food {meal_index} {ingredient_index}"
            )
            ingredients.append({"name": name, "amount": 80 + meal_index, "unit": "g"})
        meals.append(ingredients)
    return meals


def _new_service(state: _RunState) -> NutritionLookupService:
    return NutritionLookupService(
        food_ref_repo=_FoodReferences(state),
        ingredient_nutrition_resolver=_Provider(state),
        generation_service=object(),
        redis_client=_Redis(state),
        cache_service=_CacheWriter(state),
        request_timeout_seconds=5.0,
        max_concurrent_lookups=8,
    )


def _warm_cache(state: _RunState, meals: list[list[dict[str, Any]]]) -> None:
    identities = {
        normalize_food_identity(ingredient["name"])
        for meal in meals
        for ingredient in meal
    }
    for identity in identities:
        state.cache[f"{NUTRITION_CACHE_PREFIX}{identity}"] = json.dumps(CACHE_MACROS)


async def _run_once(
    *, workload: str, cache_mode: str, path: str
) -> tuple[float, int, int]:
    meals = _recipe_ingredients(workload)
    state = _RunState(provider_gate=asyncio.Semaphore(PROVIDER_CONCURRENCY))
    if cache_mode == "warm":
        _warm_cache(state, meals)
    service = _new_service(state)

    started = time.perf_counter()
    if path == "single_meal":
        results = await asyncio.gather(
            *(service.calculate_meal_macros(meal) for meal in meals)
        )
    else:
        results = await service.calculate_meals_macros(meals)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if not all(result.is_complete for result in results):
        raise RuntimeError(
            "benchmark fixture unexpectedly produced incomplete nutrition"
        )
    return elapsed_ms, state.provider_calls, state.batch_queries


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


async def main() -> None:
    print(
        "workload,cache,path,samples,p50_ms,p95_ms,provider_calls_per_request,"
        "batch_queries_per_request"
    )
    for workload in ("repeated", "distinct"):
        for cache_mode in ("cold", "warm"):
            for path in ("single_meal", "batched"):
                samples = [
                    await _run_once(workload=workload, cache_mode=cache_mode, path=path)
                    for _ in range(SAMPLE_COUNT)
                ]
                durations = [sample[0] for sample in samples]
                provider_calls = statistics.mean(sample[1] for sample in samples)
                batch_queries = statistics.mean(sample[2] for sample in samples)
                print(
                    f"{workload},{cache_mode},{path},{SAMPLE_COUNT},"
                    f"{statistics.median(durations):.2f},{_percentile(durations, 0.95):.2f},"
                    f"{provider_calls:.1f},{batch_queries:.1f}"
                )


if __name__ == "__main__":
    asyncio.run(main())
