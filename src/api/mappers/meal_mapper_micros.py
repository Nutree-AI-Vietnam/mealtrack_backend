"""Inbound/outbound micros helpers for meal mapper."""

from __future__ import annotations

from typing import Any

from src.domain.model.nutrition.extra_nutrients import extra_nutrients_to_micros
from src.domain.model.nutrition.micros import Micros
from src.domain.model.nutrition.micros_ops import (
    mapping_from_micros,
    micros_from_mapping,
)


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
