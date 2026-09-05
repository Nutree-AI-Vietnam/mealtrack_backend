"""Server-side nutrition resolution for versioned manual meal writes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

from src.app.commands.meal.create_manual_meal_command import (
    CustomNutrition,
    ManualMealItem,
)
from src.domain.model.nutrition.extra_nutrients import first_nonempty_extras
from src.domain.ports.provider_budget_port import ProviderBudgetPort
from src.domain.services.nutrition_calculation_service import (
    canonicalize_authoritative_quantity,
    fallback_custom_serving_options,
)
from src.domain.services.nutrition_integrity_policy import (
    NutritionIntegrityError,
    NutritionIntegrityPolicy,
    normalize_serving_options,
)
from src.observability import increment_metric

logger = logging.getLogger(__name__)


class ManualMealNutritionResolver:
    """Resolve v2 source identity and build immutable per-item snapshots."""

    def __init__(
        self,
        policy: NutritionIntegrityPolicy | None = None,
        provider: Any | None = None,
        provider_budget: ProviderBudgetPort | None = None,
        provider_rpm: int | None = None,
        provider_timeout_seconds: float = 5.0,
        uow_factory: Any | None = None,
    ):
        self.policy = policy or NutritionIntegrityPolicy()
        self.provider = provider
        self.provider_budget = provider_budget
        self.provider_rpm = provider_rpm
        self.provider_timeout_seconds = provider_timeout_seconds
        # Request-scoped write access for catalog adoption; the resolver is
        # reused across requests, so callers open a fresh UoW per adopt call.
        self.uow_factory = uow_factory
        self._provider_semaphore = asyncio.Semaphore(4)
        self._provider_inflight: dict[tuple[str, str], asyncio.Task] = {}
        self._provider_inflight_lock = asyncio.Lock()

    async def resolve_items(
        self,
        items: list[ManualMealItem],
        food_references: Any,
        *,
        contract_version: int,
    ) -> list[ManualMealItem]:
        if contract_version != 2:
            return items
        if len(items) > 50:
            raise ValueError("manual meal cannot contain more than 50 items")
        external_ids = {
            (
                item.origin,
                str(item.food_reference_id or item.fdc_id or item.source_food_id),
            )
            for item in items
            if item.origin in {"usda", "provider"}
        }
        if len(external_ids) > 20:
            raise ValueError("manual meal cannot resolve more than 20 external IDs")

        local_references = await self._load_local_references(items, food_references)
        usda_references = await self._load_usda_references(items, food_references)
        provider_details: dict[tuple[str, str], dict] = {}
        provider_deadline = (
            asyncio.get_running_loop().time() + self.provider_timeout_seconds
            if any(item.origin == "provider" for item in items)
            else None
        )
        resolved: list[ManualMealItem] = []
        for item in items:
            increment_metric(
                "manual_nutrition_resolution.attempt",
                attributes={"origin": item.origin or "unknown"},
            )
            try:
                if item.origin == "custom":
                    resolved_item = self._resolve_custom(item)
                elif item.origin == "local":
                    reference = local_references.get(item.food_reference_id)
                    resolved_item = self._resolve_reference(item, reference, "local")
                elif item.origin == "usda":
                    reference = usda_references.get(item.fdc_id)
                    resolved_item = self._resolve_dict_reference(
                        item, reference, "usda"
                    )
                elif item.origin == "provider":
                    resolved_item = await self._resolve_provider(
                        item, provider_details, deadline=provider_deadline
                    )
                else:
                    raise ValueError("v2 item origin is required")
                resolved_item = self._canonicalize_unmatched_unit(resolved_item)
                resolved.append(resolved_item)
            except Exception as exc:
                result = getattr(exc, "result", None)
                increment_metric(
                    "manual_nutrition_resolution.failure",
                    attributes={
                        "origin": item.origin or "unknown",
                        "reason": getattr(result, "reason_code", "validation"),
                    },
                )
                raise
            increment_metric(
                "manual_nutrition_resolution.success",
                attributes={"origin": item.origin or "unknown"},
            )
        return resolved

    @staticmethod
    def _canonicalize_unmatched_unit(item: ManualMealItem) -> ManualMealItem:
        """Keep valid source units; convert any other request unit to grams."""
        quantity, unit, used_fallback = canonicalize_authoritative_quantity(
            item.quantity,
            item.unit,
            item.allowed_units or [],
            item.name or "Food Item",
        )
        if used_fallback:
            increment_metric(
                "manual_nutrition_resolution.unit_fallback",
                attributes={"origin": item.origin or "unknown"},
            )
            return replace(item, quantity=quantity, unit=unit)
        return item

    async def revalidate_local_items(self, items, food_references) -> None:
        """Lock local references and reject a snapshot changed mid-write."""
        local_items = [item for item in items if item.origin == "local"]
        if not local_items:
            return
        batch_loader = getattr(food_references, "get_nutrition_projections", None)
        if batch_loader is None:
            return
        references = await batch_loader(
            sorted({item.food_reference_id for item in local_items}),
            for_update=True,
        )
        for item in local_items:
            reference = references.get(item.food_reference_id)
            if reference is None or not reference.is_verified:
                raise NutritionIntegrityError(
                    self.policy.rejection("reference_changed_during_write")
                )
            refreshed = self._resolve_reference(item, reference, "local")
            if refreshed.source_snapshot != item.source_snapshot:
                raise NutritionIntegrityError(
                    self.policy.rejection("reference_changed_during_write")
                )

    @staticmethod
    async def _load_local_references(items, food_references):
        ids = sorted(
            {
                item.food_reference_id
                for item in items
                if item.origin == "local" and item.food_reference_id is not None
            }
        )
        if not ids:
            return {}
        batch_loader = getattr(food_references, "get_nutrition_projections", None)
        if batch_loader is not None:
            return await batch_loader(ids)
        return {
            reference_id: await food_references.get_nutrition_projection(reference_id)
            for reference_id in ids
        }

    @staticmethod
    async def _load_usda_references(items, food_references):
        ids = sorted(
            {
                item.fdc_id
                for item in items
                if item.origin == "usda" and item.fdc_id is not None
            }
        )
        if not ids:
            return {}
        batch_loader = getattr(food_references, "get_by_fdc_ids", None)
        if batch_loader is not None:
            return await batch_loader(ids)
        return {fdc_id: await food_references.get_by_fdc_id(fdc_id) for fdc_id in ids}

    def _resolve_custom(self, item: ManualMealItem) -> ManualMealItem:
        if item.custom_nutrition is None:
            raise ValueError("custom origin requires custom nutrition")
        result = self.policy.require_valid(
            {
                "protein_100g": item.custom_nutrition.protein_per_100g,
                "carbs_100g": item.custom_nutrition.carbs_per_100g,
                "fat_100g": item.custom_nutrition.fat_per_100g,
                "fiber_100g": item.custom_nutrition.fiber_per_100g,
                "sugar_100g": item.custom_nutrition.sugar_per_100g,
            },
            require_energy=False,
            require_metric_basis=False,
        )
        allowed_units = fallback_custom_serving_options(
            item.unit,
            item.name or "",
            item.allowed_units,
        )
        return replace(
            item,
            custom_nutrition=CustomNutrition(
                calories_per_100g=result.derived_calories_100g or 0.0,
                protein_per_100g=result.protein_100g or 0.0,
                carbs_per_100g=result.carbs_100g or 0.0,
                fat_per_100g=result.fat_100g or 0.0,
                fiber_per_100g=result.fiber_100g or 0.0,
                sugar_per_100g=result.sugar_100g or 0.0,
            ),
            allowed_units=allowed_units,
            source_kind="custom",
            nutrition_contract_version="2",
            source_snapshot=self._snapshot(
                result, "custom", None, None, allowed_units
            ),
        )

    def _resolve_reference(self, item, reference, origin: str) -> ManualMealItem:
        if reference is None:
            raise ValueError("nutrition reference is unavailable")
        if item.food_reference_id != reference.id:
            raise NutritionIntegrityError(
                self.policy.rejection("reference_identity_mismatch")
            )
        if not getattr(reference, "is_verified", False):
            raise NutritionIntegrityError(self.policy.rejection("unverified_reference"))
        allowed_units = self._reference_units(reference)
        result = self.policy.require_valid(
            {
                "protein_100g": reference.protein_100g,
                "carbs_100g": reference.carbs_100g,
                "fat_100g": reference.fat_100g,
                "fiber_100g": reference.fiber_100g,
                "sugar_100g": reference.sugar_100g,
                "allowed_units": allowed_units,
            },
            require_energy=False,
            require_metric_basis=False,
        )
        source_id = str(getattr(reference, "source_food_id", None) or reference.id)
        source_namespace = (
            item.source_namespace
            or getattr(reference, "source_namespace", None)
            or getattr(reference, "source", None)
        )
        return replace(
            item,
            name=reference.name,
            custom_nutrition=self._custom_from_result(result),
            allowed_units=allowed_units,
            food_reference_id=reference.id,
            source_kind=origin,
            source_food_id=source_id,
            nutrition_contract_version="2",
            source_snapshot=self._snapshot(
                result,
                origin,
                source_namespace,
                source_id,
                allowed_units,
                canonical_name=reference.name,
                extra_nutrients=self._extras_for_snapshot(
                    getattr(reference, "extra_nutrients", None), item
                ),
            ),
        )

    def _resolve_dict_reference(self, item, reference, origin: str) -> ManualMealItem:
        if not reference or not reference.get("is_verified"):
            raise NutritionIntegrityError(self.policy.rejection("ineligible_reference"))
        if reference.get("fdc_id") not in (None, item.fdc_id):
            raise NutritionIntegrityError(
                self.policy.rejection("reference_identity_mismatch")
            )
        allowed_units = normalize_serving_options(
            reference.get("allowed_units") or reference.get("serving_sizes"),
            provider_100g_label=origin != "local",
        ) or [{"unit": "g", "gram_weight": 1.0, "description": "1 g"}]
        result = self.policy.require_valid(
            {
                "protein_100g": reference.get("protein_100g"),
                "carbs_100g": reference.get("carbs_100g"),
                "fat_100g": reference.get("fat_100g"),
                "fiber_100g": reference.get("fiber_100g", 0),
                "sugar_100g": reference.get("sugar_100g", 0),
                "calories_100g": reference.get("calories_100g"),
                "allowed_units": allowed_units,
            },
            require_energy=False,
            require_metric_basis=False,
        )
        source_id = str(item.fdc_id)
        canonical_name = reference.get("name") or reference.get("description")
        return replace(
            item,
            name=canonical_name or item.name,
            custom_nutrition=self._custom_from_result(result),
            allowed_units=allowed_units,
            source_kind=origin,
            source_food_id=source_id,
            nutrition_contract_version="2",
            source_snapshot=self._snapshot(
                result,
                origin,
                "usda_fdc",
                source_id,
                allowed_units,
                canonical_name=canonical_name,
                extra_nutrients=self._extras_for_snapshot(
                    reference.get("extra_nutrients"), item
                ),
            ),
        )

    async def _resolve_provider(
        self,
        item: ManualMealItem,
        details_cache: dict[tuple[str, str], dict],
        *,
        deadline: float | None,
    ) -> ManualMealItem:
        if self.provider is None:
            raise NutritionIntegrityError(
                self.policy.rejection("provider_resolution_unavailable")
            )
        source_id = str(item.source_food_id)
        namespace = item.source_namespace or "fatsecret"
        cache_key = (namespace, source_id)
        if cache_key not in details_cache:
            details_cache[cache_key] = await self._get_provider_details(
                source_id,
                namespace=namespace,
                deadline=deadline,
            )
        details = details_cache[cache_key]
        if not isinstance(details, dict):
            raise NutritionIntegrityError(self.policy.rejection("provider_unavailable"))
        returned_source_id = details.get("food_id") or details.get("id")
        if returned_source_id is not None and str(returned_source_id) != source_id:
            raise NutritionIntegrityError(
                self.policy.rejection("provider_identity_mismatch")
            )
        allowed_units = normalize_serving_options(
            details.get("allowed_units") or details.get("serving_sizes"),
            provider_100g_label=True,
        ) or [{"unit": "g", "gram_weight": 1.0, "description": "1 g"}]
        result = self.policy.require_valid(
            {
                "protein_100g": details.get("protein_100g"),
                "carbs_100g": details.get("carbs_100g"),
                "fat_100g": details.get("fat_100g"),
                "fiber_100g": details.get("fiber_100g", 0),
                "sugar_100g": details.get("sugar_100g", 0),
                "calories_100g": details.get("calories_100g"),
                "allowed_units": allowed_units,
            },
            require_energy=False,
            require_metric_basis=False,
            provider_100g_label=True,
        )
        english_name = str(
            details.get("food_name") or details.get("name") or item.name or "Food Item"
        ).strip()
        food_reference_id, result, allowed_units, english_name = (
            await self._adopt_provider_identity(
                namespace,
                source_id,
                english_name,
                result,
                allowed_units,
                extra_nutrients=details.get("extra_nutrients"),
            )
        )
        return replace(
            item,
            name=english_name,
            custom_nutrition=self._custom_from_result(result),
            allowed_units=allowed_units,
            food_reference_id=food_reference_id,
            source_kind="provider",
            nutrition_contract_version="2",
            source_snapshot=self._snapshot(
                result,
                "provider",
                namespace,
                source_id,
                allowed_units,
                canonical_name=english_name,
                extra_nutrients=self._extras_for_snapshot(
                    details.get("extra_nutrients"), item
                ),
            ),
        )

    async def _adopt_provider_identity(
        self,
        namespace: str,
        source_id: str,
        english_name: str,
        result: Any,
        allowed_units: list[dict[str, Any]],
        extra_nutrients: Any = None,
    ) -> tuple[int | None, Any, list[dict[str, Any]], str]:
        """Materialize a provider identity into the catalog before save.

        Save must never search FatSecret by name; this only ever adopts by
        the identity the client already supplied. When the catalog already
        has this identity verified, the adopted (frozen) density wins over
        whatever was just fetched from the provider. Adopt failures degrade
        to the freshly fetched provider density rather than blocking save.
        """
        if self.uow_factory is None:
            return None, result, allowed_units, english_name
        per_100g = {
            "protein_100g": result.protein_100g,
            "carbs_100g": result.carbs_100g,
            "fat_100g": result.fat_100g,
            "fiber_100g": result.fiber_100g,
            "sugar_100g": result.sugar_100g,
        }
        if isinstance(extra_nutrients, dict) and extra_nutrients:
            per_100g["extra_nutrients"] = extra_nutrients
        try:
            async with self.uow_factory() as uow:
                adopted = await uow.food_references.adopt_provider_food(
                    namespace,
                    source_id,
                    english_name,
                    per_100g,
                    allowed_units,
                    "en",
                    "",
                )
        except Exception:
            logger.warning(
                "manual_meal_nutrition_resolver.adopt_failed namespace=%s food_id=%s",
                namespace,
                source_id,
                exc_info=True,
            )
            return None, result, allowed_units, english_name
        if not isinstance(adopted, dict) or adopted.get("id") is None:
            return None, result, allowed_units, english_name
        adopted_units = adopted.get("allowed_units") or allowed_units
        adopted_result = self.policy.require_valid(
            {
                "protein_100g": adopted.get("protein_100g"),
                "carbs_100g": adopted.get("carbs_100g"),
                "fat_100g": adopted.get("fat_100g"),
                "fiber_100g": adopted.get("fiber_100g", 0),
                "sugar_100g": adopted.get("sugar_100g", 0),
                "allowed_units": adopted_units,
            },
            require_energy=False,
            require_metric_basis=False,
            provider_100g_label=True,
        )
        adopted_name = str(adopted.get("name") or "").strip() or english_name
        return adopted.get("id"), adopted_result, adopted_units, adopted_name

    async def _get_provider_details(
        self, source_id: str, *, namespace: str, deadline: float | None
    ):
        inflight_key = (namespace, source_id)
        async with self._provider_inflight_lock:
            task: asyncio.Task[Any] | None = self._provider_inflight.get(inflight_key)
            if task is None:
                if not await self._acquire_provider_budget(namespace):
                    raise NutritionIntegrityError(
                        self.policy.rejection("provider_budget_unavailable")
                    )
                if len(self._provider_inflight) < 256:
                    task = asyncio.create_task(self._fetch_provider_details(source_id))
                    self._provider_inflight[inflight_key] = task
        if task is None:
            task = asyncio.create_task(self._fetch_provider_details(source_id))
        try:
            timeout = self.provider_timeout_seconds
            if deadline is not None:
                timeout = max(deadline - asyncio.get_running_loop().time(), 0.001)
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        finally:
            if task.done():
                async with self._provider_inflight_lock:
                    if self._provider_inflight.get(inflight_key) is task:
                        self._provider_inflight.pop(inflight_key, None)

    async def _acquire_provider_budget(self, namespace: str) -> bool:
        if self.provider_budget is None or self.provider_rpm is None:
            return False
        try:
            return await self.provider_budget.acquire(namespace, self.provider_rpm)
        except Exception:
            return False

    async def _fetch_provider_details(self, source_id: str):
        async with self._provider_semaphore:
            provider = self.provider
            if provider is None:
                raise RuntimeError("provider resolution is unavailable")
            return await provider.get_food_details(source_id)

    @staticmethod
    def _reference_units(reference) -> list[dict[str, Any]]:
        raw = []
        for serving in getattr(reference, "servings", []) or []:
            grams = getattr(serving, "grams", None)
            if grams is not None:
                raw.append(
                    {
                        "unit": serving.name,
                        "gram_weight": grams,
                        "description": serving.name,
                    }
                )
        return normalize_serving_options(raw) or [
            {"unit": "g", "gram_weight": 1.0, "description": "1 g"}
        ]

    @staticmethod
    def _custom_from_result(result) -> CustomNutrition:
        return CustomNutrition(
            calories_per_100g=result.derived_calories_100g or 0.0,
            protein_per_100g=result.protein_100g or 0.0,
            carbs_per_100g=result.carbs_100g or 0.0,
            fat_per_100g=result.fat_100g or 0.0,
            fiber_per_100g=result.fiber_100g or 0.0,
            sugar_per_100g=result.sugar_100g or 0.0,
        )

    @staticmethod
    def ensure_source_snapshot(item: ManualMealItem) -> ManualMealItem:
        """Fill a missing snapshot from already-prepared custom density.

        Prepared custom items skip reference resolution so portion nutrition is
        not reinterpreted as per-100g. They still need an immutable snapshot
        or later v2 quantity/unit edits fail closed.
        """
        if item.source_snapshot:
            return item
        nutrition = item.custom_nutrition
        if nutrition is None:
            return item
        return replace(
            item,
            nutrition_contract_version=item.nutrition_contract_version or "2",
            source_snapshot={
                "origin": item.origin,
                "source_namespace": item.source_namespace,
                "source_food_id": item.source_food_id,
                "basis": "100g",
                "protein_per_100g": nutrition.protein_per_100g,
                "carbs_per_100g": nutrition.carbs_per_100g,
                "fat_per_100g": nutrition.fat_per_100g,
                "fiber_per_100g": nutrition.fiber_per_100g,
                "sugar_per_100g": nutrition.sugar_per_100g,
                "calories_per_100g": nutrition.calories_per_100g,
                "allowed_units": item.allowed_units
                or [{"unit": "g", "gram_weight": 1.0, "description": "1 g"}],
            },
        )

    @staticmethod
    def _extras_for_snapshot(primary: Any, item: Any) -> dict[str, Any] | None:
        incoming = None
        snapshot = getattr(item, "source_snapshot", None)
        if isinstance(snapshot, dict):
            incoming = snapshot.get("extra_nutrients")
        return first_nonempty_extras(primary, incoming)

    @staticmethod
    def _snapshot(
        result,
        origin,
        namespace,
        source_id,
        allowed_units=None,
        canonical_name=None,
        extra_nutrients=None,
    ):
        snapshot = {
            "origin": origin,
            "source_namespace": namespace,
            "source_food_id": source_id,
            "basis": "100g",
            "protein_per_100g": result.protein_100g,
            "carbs_per_100g": result.carbs_100g,
            "fat_per_100g": result.fat_100g,
            "fiber_per_100g": result.fiber_100g,
            "sugar_per_100g": result.sugar_100g,
            "calories_per_100g": result.derived_calories_100g,
            "allowed_units": allowed_units
            or [{"unit": "g", "gram_weight": 1.0, "description": "1 g"}],
        }
        clean_name = str(canonical_name or "").strip()
        if clean_name:
            snapshot["canonical_name"] = clean_name
        if isinstance(extra_nutrients, dict) and extra_nutrients:
            snapshot["extra_nutrients"] = extra_nutrients
        return snapshot
