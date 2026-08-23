"""Build a g/kg-only custom row when parse-text has no catalog or FatSecret hit."""

from __future__ import annotations

from typing import Any

from src.domain.services.nutrition_calculation_service import (
    MASS_VOLUME_CANONICAL_UNITS,
    _normalize_unit,
    canonicalize_mass_volume_unit,
    convert_quantity_to_grams,
)
from src.domain.services.nutrition_integrity_policy import NUTRITION_INTEGRITY_POLICY_VERSION
from src.domain.services.nutrition_resolver import validate_ai_fallback

CUSTOM_COUNT_GRAMS = 100.0
CUSTOM_KG_THRESHOLD_G = 1000.0
_MASS_UNITS = {"g", "gram", "grams", "kg", "kilogram", "kilograms"}
_VOLUME_UNITS = {"ml", "l", "liter", "litre"}
def apply_custom_estimate(item: dict[str, Any]) -> dict[str, Any] | None:
    """Keep AI portion macros as a custom estimate, stored in grams or kilograms.

    Countable misses bind those portion totals to 100 g × count. There is no
    trusted original gram weight, so macros are not rescaled from cup/slice
    heuristics. Density validation still drops impossible rows.
    """
    quantity_g = custom_estimate_quantity_g(item)
    if quantity_g is None:
        return None
    protein = float(item.get("protein") or 0.0)
    carbs = float(item.get("carbs") or 0.0)
    fat = float(item.get("fat") or 0.0)
    fiber = float(item.get("fiber") or 0.0)
    lookup_name = str(item.get("lookup_name") or item.get("name") or "")
    if not validate_ai_fallback(
        name=lookup_name,
        protein=protein,
        carbs=carbs,
        fat=fat,
        fiber=fiber,
        quantity_g=quantity_g,
    ):
        return None

    if quantity_g >= CUSTOM_KG_THRESHOLD_G:
        item["quantity"] = round(quantity_g / CUSTOM_KG_THRESHOLD_G, 4)
        item["unit"] = "kg"
        item["english_unit"] = "kg"
    else:
        item["quantity"] = round(quantity_g, 2)
        item["unit"] = "g"
        item["english_unit"] = "g"
    item["quantity_g"] = quantity_g
    item["origin"] = "custom"
    item["data_source"] = "custom"
    item["food_id"] = None
    item["food_reference_id"] = None
    item["source_namespace"] = None
    item["source_food_id"] = None
    item["fdc_id"] = None
    item["nutrition_basis"] = "100g"
    item["nutrition_contract_version"] = NUTRITION_INTEGRITY_POLICY_VERSION
    return item


def custom_estimate_quantity_g(item: dict[str, Any]) -> float | None:
    """Prefer stated grams; otherwise convert mass/volume or use a 100g count."""
    raw_quantity_g = item.get("quantity_g")
    if raw_quantity_g is not None:
        quantity_g = float(raw_quantity_g)
        return quantity_g if quantity_g > 0 else None
    quantity = float(item.get("quantity") or 0.0)
    if quantity <= 0:
        return None
    unit = _unit_for_custom_quantity(item)
    unit_norm = _normalize_unit(unit)
    food_name = str(item.get("lookup_name") or item.get("name") or "")
    if unit_norm in _MASS_UNITS or unit_norm in _VOLUME_UNITS:
        converted = convert_quantity_to_grams(quantity, unit, food_name)
        return converted if converted > 0 else None
    return quantity * CUSTOM_COUNT_GRAMS


def _unit_for_custom_quantity(item: dict[str, Any]) -> str:
    local = str(item.get("unit") or "").strip()
    english = str(item.get("english_unit") or "").strip()
    if local:
        canonical = canonicalize_mass_volume_unit(local)
        if canonical in MASS_VOLUME_CANONICAL_UNITS:
            return canonical
        local_norm = _normalize_unit(local)
        if local_norm in _MASS_UNITS or local_norm in _VOLUME_UNITS:
            return local
    return english or local or "serving"
