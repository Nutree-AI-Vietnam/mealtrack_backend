"""Map food-reference extra_nutrients (usually per 100g) onto Micros."""

from __future__ import annotations

from typing import Any

from src.domain.model.nutrition.micros import Micros
from src.domain.model.nutrition.micros_ops import (
    is_empty,
    mapping_from_micros,
    merge_micros,
    scale_micros,
)

_ALIASES: dict[str, str] = {
    "vitamin_a": "vitamin_a",
    "vitamin_a_mcg": "vitamin_a",
    "vit_a_mcg": "vitamin_a",
    "vitamin_c": "vitamin_c",
    "vitamin_c_mg": "vitamin_c",
    "vitamin_e": "vitamin_e",
    "vitamin_e_mg": "vitamin_e",
    "calcium": "calcium",
    "calcium_mg": "calcium",
    "iron": "iron",
    "iron_mg": "iron",
    "magnesium": "magnesium",
    "magnesium_mg": "magnesium",
    "potassium": "potassium",
    "potassium_mg": "potassium",
    "k_mg": "potassium",
    "sodium": "sodium",
    "sodium_mg": "sodium",
    "na_mg": "sodium",
    "saturated_fat": "saturated_fat",
    "saturated_fat_g": "saturated_fat",
    "sat_fat": "saturated_fat",
    "added_sugar": "added_sugar",
    "added_sugars": "added_sugar",
    "added_sugar_g": "added_sugar",
}


def first_nonempty_extras(*candidates: Any) -> dict[str, Any] | None:
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return None


def extras_from_portion_micros(micros: Any, quantity_g: float) -> dict[str, float] | None:
    """Convert portion-level AI micros into a per-100g extra_nutrients blob."""
    if quantity_g <= 0:
        return None
    return mapping_from_micros(
        extra_nutrients_to_micros(micros, factor=100.0 / quantity_g)
    )


def extra_nutrients_to_micros(
    extra: Any, *, factor: float = 1.0
) -> Micros | None:
    """Scale a per-100g extra_nutrients blob by grams/100 (`factor`)."""
    if not isinstance(extra, dict) or factor <= 0:
        return None
    parsed: dict[str, float] = {}
    for key, raw in extra.items():
        field = _ALIASES.get(str(key))
        if field is None:
            continue
        amount = _amount(raw)
        if amount is None:
            continue
        parsed[field] = parsed.get(field, 0.0) + amount
    if not parsed:
        return None
    return scale_micros(Micros.from_dict(parsed), factor)


def micros_from_snapshot(snapshot: Any, quantity_g: float) -> Micros | None:
    if not isinstance(snapshot, dict) or quantity_g <= 0:
        return None
    return extra_nutrients_to_micros(
        snapshot.get("extra_nutrients"),
        factor=quantity_g / 100.0,
    )


def micros_for_portion(
    *,
    snapshot: Any,
    quantity_g: float,
    fallback: Micros | None,
    scale_factor: float,
) -> Micros | None:
    from_snapshot = micros_from_snapshot(snapshot, quantity_g)
    if from_snapshot is not None:
        return from_snapshot
    return scale_micros(fallback, scale_factor)


def food_item_effective_micros(item: Any) -> Micros | None:
    """Prefer stored item micros; otherwise scale snapshot extras by portion grams."""
    stored = getattr(item, "micros", None)
    if not is_empty(stored):
        return stored
    return micros_from_snapshot(
        getattr(item, "source_snapshot", None),
        _portion_grams(item),
    )


def merge_meal_micros(
    nutrition_micros: Micros | None,
    food_items: list | None,
) -> Micros | None:
    from_items = merge_micros(
        *(food_item_effective_micros(item) for item in food_items or [])
    )
    if not is_empty(from_items):
        return from_items
    if not is_empty(nutrition_micros):
        return nutrition_micros
    return None


def _portion_grams(item: Any) -> float:
    try:
        quantity = float(getattr(item, "quantity", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if quantity <= 0:
        return 0.0
    unit = str(getattr(item, "unit", "") or "g").strip().lower()
    if unit in {"g", "gram", "grams", "gramme", "grammes"}:
        return quantity
    options = list(getattr(item, "allowed_units", None) or [])
    snapshot = getattr(item, "source_snapshot", None)
    if isinstance(snapshot, dict) and not options:
        options = list(snapshot.get("allowed_units") or [])
    for option in options:
        if not isinstance(option, dict):
            continue
        if str(option.get("unit") or "").strip().lower() != unit:
            continue
        try:
            return quantity * float(option.get("gram_weight") or 0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _amount(raw: Any) -> float | None:
    if isinstance(raw, dict):
        raw = raw.get("amount")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number
