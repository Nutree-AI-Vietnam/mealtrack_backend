"""Optional local-first nutrition reference validation for meal scans."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from src.domain.model.meal import Meal
from src.domain.model.nutrition.extra_nutrients import extra_nutrients_to_micros
from src.domain.model.nutrition.micros_ops import is_empty, merge_micros
from src.domain.ports.nutrition_reference_provider_port import (
    NutritionReferenceProviderPort,
)
from src.domain.services.nutrition_integrity_policy import NutritionIntegrityPolicy

FoodReferenceLookup = Callable[[str], Awaitable[dict[str, Any] | None]]
FoodReferenceBatchLookup = Callable[[list[str]], Awaitable[dict[str, dict[str, Any]]]]
MACRO_DIVERGENCE_TOLERANCE = 0.35
_REFERENCE_SOURCES = frozenset(
    {"local", "fatsecret", "openfoodfacts", "provider", "usda"}
)
ValidationOutcome = Literal[
    "match", "diverge", "timeout", "error", "skipped", "unresolved"
]
ValidationItemOutcome = Literal["match", "diverge", "unresolved"]


@dataclass(frozen=True)
class ValidationItemReport:
    """One food item's reference check.

    Later grounding and cascade work should add optional fields with defaults.
    ``outcome`` and ``source`` stay the stable per-item contract.
    """

    item_id: str
    outcome: ValidationItemOutcome
    source: str | None
    reason_code: str

    def to_audit_dict(self) -> dict[str, str | None]:
        return {
            "item_id": self.item_id,
            "outcome": self.outcome,
            "source": self.source,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class ValidationReport:
    """Best-effort reference validation.

    ``meal`` is always safe to persist. Timeouts and provider errors are
    recorded on this report instead of being discarded or failing the scan.
    Add optional fields with defaults when a follow-up needs grounding or
    cascade details; do not replace ``outcome`` or drop ``items``.
    """

    meal: Meal
    outcome: ValidationOutcome
    items: tuple[ValidationItemReport, ...] = ()
    reason_code: str | None = None
    error_type: str | None = None

    def to_audit_dict(self) -> dict[str, Any]:
        """JSON-safe summary. Omits the meal so logs cannot carry food text."""
        return {
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "error_type": self.error_type,
            "items": [item.to_audit_dict() for item in self.items],
        }


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

    async def validate_meal(self, meal: Meal) -> ValidationReport:
        """Validate without dropping a meal when a reference check fails."""
        if meal.source == "food_label":
            return ValidationReport(
                meal=meal, outcome="skipped", reason_code="food_label"
            )
        if not meal.nutrition:
            return ValidationReport(
                meal=meal, outcome="skipped", reason_code="missing_nutrition"
            )
        if not meal.nutrition.food_items:
            return ValidationReport(
                meal=meal, outcome="skipped", reason_code="no_food_items"
            )

        item_results: list[ValidationItemReport] = []
        try:
            await asyncio.wait_for(
                self._validate_items(meal, item_results),
                self._timeout_seconds,
            )
        except TimeoutError:
            return ValidationReport(
                meal=meal,
                outcome="timeout",
                reason_code="timeout",
                error_type="TimeoutError",
                items=tuple(item_results),
            )
        except Exception as exc:
            return ValidationReport(
                meal=meal,
                outcome="error",
                reason_code="provider_error",
                error_type=type(exc).__name__,
                items=tuple(item_results),
            )
        return ValidationReport(
            meal=meal,
            outcome=_aggregate_outcome(item_results),
            items=tuple(item_results),
        )

    async def _validate_items(
        self,
        meal: Meal,
        item_results: list[ValidationItemReport],
    ) -> None:
        nutrition = meal.nutrition
        if nutrition is None or not nutrition.food_items:
            return
        food_items = nutrition.food_items
        item_names = {
            item.name: self._normalize_name(item.name)
            for item in food_items
            if self._normalize_name(item.name)
        }
        local_references = await self._find_local_references(list(item_names.values()))

        for item in food_items:
            normalized_name = self._normalize_name(item.name)
            if not normalized_name:
                item_results.append(
                    ValidationItemReport(
                        item_id=item.id,
                        outcome="unresolved",
                        source=None,
                        reason_code="empty_name",
                    )
                )
                continue

            local_match = local_references.get(normalized_name)
            if local_match:
                item_results.append(
                    self._result_for_reference(item, local_match, fallback="local")
                )
                continue

            if self._nutrition_reference_provider is None:
                item_results.append(
                    ValidationItemReport(
                        item_id=item.id,
                        outcome="unresolved",
                        source=None,
                        reason_code="no_provider",
                    )
                )
                continue

            candidates = (
                await self._nutrition_reference_provider.search_food_candidates(
                    item.name,
                    max_results=3,
                )
            )
            selected = self._select_candidate(candidates, item.name)
            if not selected or not selected.get("food_id"):
                item_results.append(
                    ValidationItemReport(
                        item_id=item.id,
                        outcome="unresolved",
                        source=None,
                        reason_code="no_candidate",
                    )
                )
                continue

            details = await self._nutrition_reference_provider.get_food_details(
                str(selected["food_id"])
            )
            if not details:
                item_results.append(
                    ValidationItemReport(
                        item_id=item.id,
                        outcome="unresolved",
                        source="provider",
                        reason_code="no_details",
                    )
                )
                continue
            item_results.append(
                self._result_for_reference(item, details, fallback="provider")
            )

        nutrition.micros = merge_micros(*(item.micros for item in food_items))

    def _result_for_reference(
        self,
        item: Any,
        reference: dict[str, Any],
        *,
        fallback: str,
    ) -> ValidationItemReport:
        applied, reason_code = self._apply_reference_if_close(item, reference)
        if applied:
            outcome: ValidationItemOutcome = "match"
        elif reason_code == "macro_divergence":
            outcome = "diverge"
        else:
            outcome = "unresolved"
        return ValidationItemReport(
            item_id=item.id,
            outcome=outcome,
            source=_bounded_source(reference, fallback),
            reason_code=reason_code,
        )

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

    def _apply_reference_if_close(
        self, item: Any, reference: dict[str, Any]
    ) -> tuple[bool, str]:
        source = str(reference.get("source") or "").lower()
        integrity = self._integrity_policy.evaluate(
            reference,
            require_energy=False,
            require_metric_basis=False,
            provider_100g_label=source in {"fatsecret", "openfoodfacts", "provider"},
        )
        if not integrity.accepted:
            return False, integrity.reason_code
        if not self._is_macro_close(item, reference):
            return False, "macro_divergence"

        if integrity.serving_options:
            item.allowed_units = list(integrity.serving_options)
        self._attach_reference_micros(item, reference)
        return True, "matched"

    def _attach_reference_micros(self, item: Any, reference: dict[str, Any]) -> None:
        extras = reference.get("extra_nutrients")
        if not isinstance(extras, dict) or not extras:
            return
        try:
            quantity_g = float(item.quantity or 0)
        except (TypeError, ValueError):
            return
        micros = extra_nutrients_to_micros(extras, factor=quantity_g / 100.0)
        if micros is None:
            return
        if is_empty(getattr(item, "micros", None)):
            item.micros = micros
        snapshot = dict(getattr(item, "source_snapshot", None) or {})
        snapshot.setdefault("basis", "100g")
        snapshot["extra_nutrients"] = extras
        item.source_snapshot = snapshot

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


def _aggregate_outcome(
    items: list[ValidationItemReport],
) -> Literal["match", "diverge", "unresolved"]:
    outcomes = {item.outcome for item in items}
    if "diverge" in outcomes:
        return "diverge"
    if "match" in outcomes:
        return "match"
    return "unresolved"


def _bounded_source(reference: dict[str, Any], fallback: str) -> str:
    raw = str(reference.get("source") or "").strip().lower()
    if raw in _REFERENCE_SOURCES:
        return raw
    return fallback
