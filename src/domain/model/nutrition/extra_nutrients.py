"""Map food-reference extra_nutrients (usually per 100g) onto Micros."""

from __future__ import annotations

from typing import Any

from src.domain.model.nutrition.micros import Micros
from src.domain.model.nutrition.micros_ops import is_empty, merge_micros, scale_micros

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


def merge_meal_micros(
    nutrition_micros: Micros | None,
    food_items: list | None,
) -> Micros | None:
    from_items = merge_micros(
        *(getattr(item, "micros", None) for item in food_items or [])
    )
    if not is_empty(from_items):
        return from_items
    if not is_empty(nutrition_micros):
        return nutrition_micros
    return None


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
