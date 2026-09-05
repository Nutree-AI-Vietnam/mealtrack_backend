"""Inbound/outbound micros helpers for meal mapper."""

from __future__ import annotations

from typing import Any

from src.api.schemas.response.daily_nutrition_response import (
    MicronutrientTargetsResponse,
)
from src.domain.model.nutrition.extra_nutrients import extra_nutrients_to_micros
from src.domain.model.nutrition.micros import Micros
from src.domain.model.nutrition.micros_ops import (
    mapping_from_micros,
    micros_from_mapping,
)
from src.domain.services.micronutrient_dri_targets import micronutrient_daily_targets


def micros_from_nutrition_payload(data: dict[str, Any] | None) -> Micros | None:
    """Parse a full micros blob or aliased extra_nutrients keys (not sodium-only)."""
    if not data:
        return None
    nested = data.get("micros")
    if isinstance(nested, dict):
        parsed = micros_from_mapping(nested)
        if parsed is not None:
            return parsed
    return extra_nutrients_to_micros(data)


def micros_map_for_response(micros: Micros | None) -> dict[str, float] | None:
    return mapping_from_micros(micros)


def daily_micro_targets(
    daily_macros_data: dict[str, Any],
    target_calories: float,
) -> MicronutrientTargetsResponse:
    cached = daily_macros_data.get("micro_targets")
    if isinstance(cached, dict) and cached:
        return MicronutrientTargetsResponse.model_validate(cached)
    dri = micronutrient_daily_targets(
        gender=daily_macros_data.get("gender"),
        age_years=daily_macros_data.get("age"),
        plan_calories=target_calories,
    )
    return MicronutrientTargetsResponse(
        iron_mg=dri.iron_mg,
        fiber_g=dri.fiber_g,
        potassium_mg=dri.potassium_mg,
        sodium_mg=dri.sodium_mg,
        added_sugar_g=dri.added_sugar_g,
    )
