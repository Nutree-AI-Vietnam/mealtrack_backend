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
from src.domain.services.micronutrient_dri_targets import micronutrient_daily_goals


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
    personal = micronutrient_daily_goals(
        gender=daily_macros_data.get("gender"),
        age=daily_macros_data.get("age"),
        plan_calories=target_calories,
        dietary_preferences=daily_macros_data.get("dietary_preferences"),
    )
    return MicronutrientTargetsResponse(
        iron_mg=personal.goals["iron"],
        fiber_g=personal.fiber_g,
        potassium_mg=personal.goals["potassium"],
        sodium_mg=personal.goals["sodium"],
        added_sugar_g=personal.goals["added_sugar"],
    )
