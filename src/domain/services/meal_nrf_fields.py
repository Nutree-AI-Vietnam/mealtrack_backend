"""Per-item NRF fields for meal GET and activity list chips.

Uses ``nrf_quality`` when coverage >= 1. Do not call ``nrf_progress_fields``
here — that helper zeros quality below coverage 4 (day blend only).
"""

from __future__ import annotations

from typing import Any

from src.domain.model.nutrition.extra_nutrients import merge_meal_micros
from src.domain.services.nrf_score import nrf_coverage, nrf_quality


def meal_nrf_fields(meal: Any) -> dict[str, float | int | None]:
    nutrition = getattr(meal, "nutrition", None)
    if nutrition is None:
        return {"nrf_quality": None, "nrf_coverage": 0}
    macros = getattr(nutrition, "effective_macros", None) or getattr(
        nutrition, "macros", None
    )
    protein = float(getattr(macros, "protein", 0) or 0) if macros else 0.0
    fiber = float(getattr(macros, "fiber", 0) or 0) if macros else 0.0
    merged = merge_meal_micros(
        getattr(nutrition, "micros", None),
        getattr(nutrition, "food_items", None),
    )
    coverage = nrf_coverage(merged)
    quality = nrf_quality(protein, fiber, merged) if coverage >= 1 else None
    return {"nrf_quality": quality, "nrf_coverage": coverage}


def hydration_entry_nrf_fields(entry: Any) -> dict[str, float | int | None]:
    micros = getattr(entry, "micros", None)
    coverage = nrf_coverage(micros)
    if coverage < 1:
        return {"nrf_quality": None, "nrf_coverage": coverage}
    quality = nrf_quality(
        float(getattr(entry, "protein_g", 0) or 0),
        float(getattr(entry, "fiber_g", 0) or 0),
        micros,
    )
    return {"nrf_quality": quality, "nrf_coverage": coverage}
