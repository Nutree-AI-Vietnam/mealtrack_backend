"""Resolve a single FatSecret (provider) food via food.get.v5 on select."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

from src.app.events.base import EventHandler, handles
from src.app.queries.food.get_provider_food_details_query import (
    GetProviderFoodDetailsQuery,
)
from src.domain.cache.cache_keys import CacheKeys
from src.domain.constants.fatsecret_locale import LANGUAGE_TO_REGION
from src.domain.services.nutrition_integrity_policy import NutritionIntegrityError
from src.observability import distribution_metric, increment_metric

logger = logging.getLogger(__name__)

SUPPORTED_NAMESPACES = frozenset({"fatsecret"})


@handles(GetProviderFoodDetailsQuery)
class GetProviderFoodDetailsQueryHandler(
    EventHandler[GetProviderFoodDetailsQuery, dict[str, Any]]
):
    """Fetch full provider nutrition for one selected search candidate."""

    def __init__(
        self,
        mapping_service,
        fat_secret_service: Any | None = None,
        uow_factory: Any | None = None,
        cache_service: Any | None = None,
    ):
        self.mapping_service = mapping_service
        self.fat_secret_service = fat_secret_service
        self.uow_factory = uow_factory
        self.cache_service = cache_service

    async def handle(self, event: GetProviderFoodDetailsQuery) -> dict[str, Any]:
        started = perf_counter()
        namespace = (event.source_namespace or "").strip().lower()
        source_food_id = str(event.source_food_id or "").strip()
        if namespace not in SUPPORTED_NAMESPACES or not source_food_id:
            self._record(started, status="invalid")
            raise ValueError("unsupported provider food identity")

        if self.fat_secret_service is None:
            self._record(started, status="unavailable")
            raise LookupError("fatsecret provider is not configured")

        language = event.language or "en"
        region = LANGUAGE_TO_REGION.get(language, "US")
        detail_language = language if language in LANGUAGE_TO_REGION else "en"
        cache_id = f"provider:{namespace}:{source_food_id}:{detail_language}"

        cached = await self._read_cache(cache_id)
        if cached is not None:
            mapped = self._map_item(cached)
            if mapped is not None:
                self._record(started, status="cache")
                return mapped

        details = await self.fat_secret_service.get_food_details(
            source_food_id,
            region=region,
            language=detail_language,
        )
        if not details:
            self._record(started, status="empty")
            raise LookupError("provider food not found")

        details.setdefault("source", "fatsecret")
        details.setdefault("source_namespace", "fatsecret")
        details.setdefault("source_food_id", source_food_id)
        details.setdefault("food_id", source_food_id)
        details.setdefault("origin", "provider")

        if event.adopt:
            await self._adopt(details, language=language)

        mapped = self._map_item(details)
        if mapped is None:
            self._record(started, status="rejected")
            raise LookupError("provider food failed nutrition integrity")

        await self._write_cache(cache_id, details)
        self._record(started, status="success")
        return mapped

    def _map_item(self, item: dict[str, Any]) -> dict[str, Any] | None:
        try:
            mapped = self.mapping_service.map_search_item(item)
        except NutritionIntegrityError as exc:
            logger.info(
                "provider food details rejected by integrity: %s",
                exc.result.reason_code,
            )
            return None
        if item.get("food_reference_id") is not None:
            mapped["food_reference_id"] = item["food_reference_id"]
        return mapped

    async def _adopt(self, item: dict[str, Any], *, language: str) -> None:
        if self.uow_factory is None:
            return
        if item.get("metric_serving_amount") is None:
            return
        if not all(
            item.get(field) is not None
            for field in ("protein_100g", "carbs_100g", "fat_100g")
        ):
            return
        display_name = str(item.get("description") or item.get("name") or "")
        english_name = str(item.get("canonical_name") or display_name)
        try:
            async with self.uow_factory() as uow:
                adopted = await uow.food_references.adopt_provider_food(
                    item.get("source_namespace") or "fatsecret",
                    str(item.get("source_food_id") or item.get("food_id")),
                    english_name,
                    {
                        "protein_100g": item.get("protein_100g"),
                        "carbs_100g": item.get("carbs_100g"),
                        "fat_100g": item.get("fat_100g"),
                        "fiber_100g": item.get("fiber_100g") or 0,
                        "sugar_100g": item.get("sugar_100g") or 0,
                    },
                    item.get("allowed_units"),
                    language,
                    display_name,
                )
            item["food_reference_id"] = adopted.get("id")
        except Exception:
            logger.warning("provider food adopt failed", exc_info=True)

    async def _read_cache(self, cache_id: str) -> dict[str, Any] | None:
        redis = getattr(self.cache_service, "cache_service", None)
        if redis is None:
            return None
        try:
            cache_key, _ = CacheKeys.food_details(cache_id)
            return await redis.get_json(cache_key)
        except Exception:
            logger.warning("provider food cache read failed", exc_info=True)
            return None

    async def _write_cache(self, cache_id: str, payload: dict[str, Any]) -> None:
        redis = getattr(self.cache_service, "cache_service", None)
        if redis is None:
            return
        try:
            cache_key, default_ttl = CacheKeys.food_details(cache_id)
            await redis.set_json(cache_key, payload, default_ttl)
        except Exception:
            logger.warning("provider food cache write failed", exc_info=True)

    def _record(self, started: float, *, status: str) -> None:
        attributes = {
            "operation": "provider_details",
            "source": "fatsecret",
            "status": status,
        }
        distribution_metric(
            "food_search.operation.latency_ms",
            (perf_counter() - started) * 1000,
            unit="millisecond",
            attributes=attributes,
        )
        increment_metric("food_search.requests", attributes=attributes)
