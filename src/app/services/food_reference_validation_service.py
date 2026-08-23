"""Optional local-first nutrition reference validation for meal scans."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from src.domain.model.meal import Meal
from src.domain.ports.nutrition_reference_provider_port import (
    NutritionReferenceProviderPort,
)
from src.domain.services.nutrition_integrity_policy import NutritionIntegrityPolicy

FoodReferenceLookup = Callable[[str], Awaitable[dict[str, Any] | None]]
FoodReferenceBatchLookup = Callable[[list[str]], Awaitable[dict[str, dict[str, Any]]]]
MACRO_DIVERGENCE_TOLERANCE = 0.35


class FoodReferenceValidationService:
    """Validate meal scan food items against local references before providers."""

    def __init__(
        self,
        *,
        food_reference_repository: Any | None = None,
        food_reference_lookup: FoodReferenceLookup | None = None,
        food_reference_batch_lookup: FoodReferenceBatchLookup | None = None,
        nutrition_reference_provider: NutritionReferenceProviderPort | None = None,
        timeout_seconds: float = 5.0,
        integrity_policy: NutritionIntegrityPolicy | None = None,
    ):
        self._food_reference_repository = food_reference_repository
        self._food_reference_lookup = food_reference_lookup
        self._food_reference_batch_lookup = food_reference_batch_lookup
        self._nutrition_reference_provider = nutrition_reference_provider
        self._timeout_seconds = timeout_seconds
        self._integrity_policy = integrity_policy or NutritionIntegrityPolicy()

    async def validate_meal(self, meal: Meal) -> Meal:
        """Best-effort validation that never blocks a valid meal from returning."""
        if (
            meal.source == "food_label"
            or not meal.nutrition
            or not meal.nutrition.food_items
        ):
            return meal

        try:
            await asyncio.wait_for(self._validate_items(meal), self._timeout_seconds)
        except Exception:
            return meal
        return meal

    async def _validate_items(self, meal: Meal) -> None:
        item_names = {
            item.name: self._normalize_name(item.name)
            for item in meal.nutrition.food_items or []
            if self._normalize_name(item.name)
        }
        local_references = await self._find_local_references(list(item_names.values()))

        for item in meal.nutrition.food_items or []:
            normalized_name = self._normalize_name(item.name)
            if not normalized_name:
                continue

            local_match = local_references.get(normalized_name)
            if local_match:
                self._apply_reference_if_close(item, local_match)
                continue

            if self._nutrition_reference_provider is None:
                continue

            candidates = (
                await self._nutrition_reference_provider.search_food_candidates(
                    item.name,
                    max_results=3,
                )
            )
            selected = self._select_candidate(candidates, item.name)
            if selected and selected.get("food_id"):
                details = await self._nutrition_reference_provider.get_food_details(
                    str(selected["food_id"])
                )
                if details:
                    self._apply_reference_if_close(item, details)

    def _select_candidate(
        self,
        candidates: list[dict[str, Any]],
        item_name: str,
    ) -> dict[str, Any] | None:
        normalized_item = self._normalize_name(item_name)
        for candidate in candidates:
            candidate_name = str(
                candidate.get("description") or candidate.get("food_name") or ""
            )
            if self._normalize_name(candidate_name) == normalized_item:
                return candidate
        return candidates[0] if candidates else None

    async def _find_local_reference(
        self,
        normalized_name: str,
    ) -> dict[str, Any] | None:
        if self._food_reference_lookup is not None:
            return await self._food_reference_lookup(normalized_name)
        if self._food_reference_repository is not None:
            return await self._food_reference_repository.find_by_normalized_name(
                normalized_name
            )
        return None

    async def _find_local_references(
        self,
        normalized_names: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not normalized_names:
            return {}
        unique_names = sorted(set(normalized_names))
        if self._food_reference_batch_lookup is not None:
            return await self._food_reference_batch_lookup(unique_names)
        if self._food_reference_repository is not None and hasattr(
            self._food_reference_repository, "find_batch_by_normalized_names"
        ):
            return await self._food_reference_repository.find_batch_by_normalized_names(
                unique_names
            )

        references: dict[str, dict[str, Any]] = {}
        for name in unique_names:
            reference = await self._find_local_reference(name)
            if reference:
                references[name] = reference
        return references

    def _apply_reference_if_close(self, item: Any, reference: dict[str, Any]) -> bool:
        source = str(reference.get("source") or "").lower()
        integrity = self._integrity_policy.evaluate(
            reference,
            require_energy=False,
            require_metric_basis=False,
            provider_100g_label=source in {"fatsecret", "openfoodfacts", "provider"},
        )
        if not integrity.accepted:
            return False
        if not self._is_macro_close(item, reference):
            return False

        return True

    def _is_macro_close(self, item: Any, reference: dict[str, Any]) -> bool:
        quantity = float(item.quantity)
        if quantity <= 0:
            return False

        comparisons = (
            (item.macros.protein, reference.get("protein_100g")),
            (item.macros.carbs, reference.get("carbs_100g")),
            (item.macros.fat, reference.get("fat_100g")),
        )

        for total_value, reference_per_100g in comparisons:
            if reference_per_100g is None:
                return False
            item_per_100g = (float(total_value) / quantity) * 100
            reference_value = float(reference_per_100g)
            denominator = max(reference_value, 1.0)
            if (
                abs(item_per_100g - reference_value) / denominator
                > MACRO_DIVERGENCE_TOLERANCE
            ):
                return False
        return True

    def _normalize_name(self, value: str) -> str:
        return re.sub(r"\s+", " ", value.strip().lower())
