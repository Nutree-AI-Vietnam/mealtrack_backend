"""Load curated popular staples from food_reference (no FatSecret)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Awaitable
from typing import Any

from src.api.mappers.food_reference_display_name import (
    resolve_food_reference_display_name,
)
from src.app.events.base import EventHandler, handles
from src.app.queries.food.get_popular_staples_query import (
    POPULAR_STAPLE_SOURCE_IDENTITIES,
    GetPopularStaplesQuery,
)
from src.domain.constants.languages import normalize_language
from src.domain.ports.food_mapping_service_port import FoodMappingServicePort
from src.domain.services.nutrition_integrity_policy import NutritionIntegrityError

logger = logging.getLogger(__name__)

LoadBySourceIdentities = Callable[
    [list[tuple[str, str]]], Awaitable[list[dict[str, Any]]]
]


@handles(GetPopularStaplesQuery)
class GetPopularStaplesQueryHandler(
    EventHandler[GetPopularStaplesQuery, dict[str, Any]]
):
    """Return fixed food_reference staples in curated order."""

    def __init__(
        self,
        mapping_service: FoodMappingServicePort,
        load_by_source_identities: LoadBySourceIdentities,
    ):
        self.mapping_service = mapping_service
        self.load_by_source_identities = load_by_source_identities

    async def handle(self, event: GetPopularStaplesQuery) -> dict[str, Any]:
        language = normalize_language(event.language)
        identities = list(POPULAR_STAPLE_SOURCE_IDENTITIES)
        try:
            rows = await self.load_by_source_identities(identities)
        except Exception:
            logger.warning("popular staples load failed", exc_info=True)
            return {"results": [], "total": 0}

        by_identity = {
            (
                str(row.get("source_namespace") or ""),
                str(row.get("source_food_id") or ""),
            ): row
            for row in rows
            if isinstance(row, dict)
        }
        mapped: list[dict[str, Any]] = []
        for namespace, food_id in identities:
            row = by_identity.get((namespace, food_id))
            if row is None:
                continue
            raw = self._to_raw(row, language=language)
            try:
                mapped.append(self.mapping_service.map_search_item(raw))
            except NutritionIntegrityError:
                logger.info(
                    "popular staple skipped by integrity",
                    extra={
                        "source_namespace": namespace,
                        "source_food_id": food_id,
                    },
                )
            except Exception:
                logger.warning(
                    "popular staple map failed",
                    extra={
                        "source_namespace": namespace,
                        "source_food_id": food_id,
                    },
                    exc_info=True,
                )

        return {"results": mapped, "total": len(mapped)}

    def _to_raw(self, row: dict[str, Any], *, language: str) -> dict[str, Any]:
        display_name = resolve_food_reference_display_name(row, language)
        allowed_units = self._localize_units(
            row.get("allowed_units") or [],
            language=language,
        )
        return {
            "source": "food_reference",
            "food_reference_id": row["id"],
            "origin": "local",
            "source_namespace": row.get("source_namespace") or "food_reference",
            "source_food_id": row.get("source_food_id") or str(row["id"]),
            "food_id": f"food_reference:{row['id']}",
            "description": display_name,
            "name": display_name,
            "name_vi": row.get("name_vi"),
            "brand": row.get("brand"),
            "provider_source": row.get("source"),
            "is_verified": row.get("is_verified"),
            "serving_description": row.get("serving_size"),
            "allowed_units": allowed_units,
            "protein_100g": row.get("protein_100g"),
            "carbs_100g": row.get("carbs_100g"),
            "fat_100g": row.get("fat_100g"),
            "fiber_100g": row.get("fiber_100g"),
            "sugar_100g": row.get("sugar_100g"),
        }

    @staticmethod
    def _localize_units(units: Any, *, language: str) -> list[dict[str, Any]]:
        if not isinstance(units, list):
            return []
        keep_localized = language == "vi"
        cleaned: list[dict[str, Any]] = []
        for raw in units:
            if not isinstance(raw, dict):
                continue
            unit = dict(raw)
            unit = GetPopularStaplesQueryHandler._normalize_ml_portion(unit)
            if not keep_localized:
                unit.pop("display_description", None)
            cleaned.append(unit)
        return cleaned

    @staticmethod
    def _normalize_ml_portion(unit: dict[str, Any]) -> dict[str, Any]:
        """Treat provider ml rows with ~100g weight as a 100 ml portion."""
        name = str(unit.get("unit") or "").strip().lower()
        if name != "ml":
            return unit
        try:
            grams = float(unit.get("gram_weight") or 0)
        except (TypeError, ValueError):
            return unit
        description = str(unit.get("description") or "").strip().lower()
        # Only rewrite empty/trivial labels — keep explicit portions like "250 ml".
        if grams > 2.5 and (not description or description in {"ml", "1 ml"}):
            fixed = dict(unit)
            fixed["description"] = "100 ml"
            return fixed
        return unit
