"""
Ingredient nutrition resolver — T2 lookup via fatsecret.

Wraps FatSecretService to provide per-100g macros for individual
ingredients. Every cache hit is persisted to food_reference so T1
warms automatically over time.
"""

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.domain.services.meal_suggestion.ingredient_name_normalizer import (
    normalize_food_name,
)

logger = logging.getLogger(__name__)


@dataclass
class PerHundredGramsMacros:
    """Per-100g macro breakdown returned by the resolver."""

    protein: float
    carbs: float
    fat: float
    fiber: float = field(default=0.0)
    sugar: float = field(default=0.0)


class IngredientNutritionResolver:
    """Resolve per-100g macros for a named ingredient via fatsecret.

    Lookup strategy:
    1. Call FatSecretService.search_foods(query=name, max_results=5).
    2. Pick the best result, preferring food_type == "Generic" over branded.
    3. Extract per-100g macros from fatsecret's servings data.
    4. Upsert into food_reference with source="fatsecret", is_verified=False.
    5. Return PerHundredGramsMacros, or None on any failure / empty result.
    """

    def __init__(self, fatsecret: Any, food_ref_repo: Any) -> None:
        self._fs = fatsecret
        self._repo = food_ref_repo

    async def resolve(
        self, name: str, *, name_normalized: str | None = None
    ) -> Optional[PerHundredGramsMacros]:
        """Resolve per-100g macros for the given ingredient name.

        Returns None if fatsecret returns no results, hits rate limits,
        or raises any network exception.
        """
        if self._fs is None:
            logger.debug("fatsecret provider unavailable for ingredient '%s'", name)
            return None

        try:
            results: List[Dict[str, Any]] = await self._fs.search_foods(
                query=name, max_results=5
            )
        except Exception as exc:
            logger.warning("fatsecret search failed for ingredient '%s': %s", name, exc)
            return None

        if not results:
            logger.debug("fatsecret returned no results for ingredient '%s'", name)
            return None

        best = self._pick_generic(results)
        if best is None:
            logger.debug("No usable result from fatsecret for ingredient '%s'", name)
            return None

        macros = self._extract_macros(best)
        if macros is None:
            logger.debug("Could not extract macros for ingredient '%s'", name)
            return None

        await self._upsert_food_reference(name, best, macros, name_normalized)
        return macros

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_generic(results: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Return the best result, preferring food_type == 'Generic'.

        Falls back to the first result if no generic entry is found.
        Returns None only if results is empty.
        """
        if not results:
            return None

        for result in results:
            food_type = result.get("food_type", "")
            if str(food_type).lower() == "generic":
                return result

        # No generic found — fall back to first result
        return results[0]

    @staticmethod
    def _extract_macros(food: Dict[str, Any]) -> Optional[PerHundredGramsMacros]:
        """Extract per-100g macros from a fatsecret search result dict.

        FatSecretService.search_foods already enriches each result with
        per-100g keys (protein_100g, carbs_100g, fat_100g) via
        _extract_nutrition_from_details. Falls back to None if any
        required key is missing.
        """
        protein = food.get("protein_100g")
        carbs = food.get("carbs_100g")
        fat = food.get("fat_100g")

        if protein is None or carbs is None or fat is None:
            return None

        try:
            return PerHundredGramsMacros(
                protein=float(protein),
                carbs=float(carbs),
                fat=float(fat),
                fiber=0.0,
                sugar=0.0,
            )
        except (TypeError, ValueError):
            return None

    async def _upsert_food_reference(
        self,
        name: str,
        food: Dict[str, Any],
        macros: PerHundredGramsMacros,
        name_normalized: str | None = None,
    ) -> None:
        """Persist macro lookup to food_reference (T1 cache warm-up)."""
        normalized = name_normalized or normalize_food_name(name)
        external_id: Optional[str] = food.get("food_id")

        try:
            kwargs = {
                "name": name,
                "name_normalized": normalized,
                "protein_100g": macros.protein,
                "carbs_100g": macros.carbs,
                "fat_100g": macros.fat,
                "fiber_100g": macros.fiber,
                "sugar_100g": macros.sugar,
                "source": "fatsecret",
                "is_verified": False,
                "external_id": external_id,
            }
            if inspect.iscoroutinefunction(self._repo.upsert_by_normalized_name):
                await self._repo.upsert_by_normalized_name(**kwargs)
            else:
                await asyncio.to_thread(
                    self._repo.upsert_by_normalized_name,
                    **kwargs,
                )
        except Exception as exc:
            # Non-fatal: cache warm-up failure must not break the resolver
            logger.warning("Failed to upsert food_reference for '%s': %s", name, exc)
