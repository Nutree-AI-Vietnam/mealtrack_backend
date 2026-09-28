"""Map food-reference extra_nutrients (usually per 100g) onto Micros."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from src.domain.model.nutrition.micros import Micros
from src.domain.model.nutrition.micros_ops import (
    is_empty,
    mapping_from_micros,
    merge_micros,
    merge_micros_complete,
    scale_micros,
)
from src.domain.model.nutrition.nutrient_units import convert_nutrient_amount

_ALIASES: dict[str, str] = {
    "vitamin_a": "vitamin_a",
    "vitamin_a_mcg": "vitamin_a",
    "vit_a_mcg": "vitamin_a",
    "vitamin_c": "vitamin_c",
    "vitamin_c_mg": "vitamin_c",
    "vitamin_d": "vitamin_d",
    "vitamin_d_mcg": "vitamin_d",
    "vitamin_e": "vitamin_e",
    "vitamin_e_mg": "vitamin_e",
    "vitamin_k": "vitamin_k",
    "vitamin_k_mcg": "vitamin_k",
    "thiamin": "thiamin",
    "thiamin_mg": "thiamin",
    "riboflavin": "riboflavin",
    "riboflavin_mg": "riboflavin",
    "niacin": "niacin",
    "niacin_mg": "niacin",
    "vitamin_b6": "vitamin_b6",
    "vitamin_b6_mg": "vitamin_b6",
    "vitamin_b12": "vitamin_b12",
    "vitamin_b12_mcg": "vitamin_b12",
    "folate": "folate",
    "folate_mcg": "folate",
    "calcium": "calcium",
    "calcium_mg": "calcium",
    "iron": "iron",
    "iron_mg": "iron",
    "magnesium": "magnesium",
    "magnesium_mg": "magnesium",
    "phosphorus": "phosphorus",
    "phosphorus_mg": "phosphorus",
    "potassium": "potassium",
    "potassium_mg": "potassium",
    "k_mg": "potassium",
    "sodium": "sodium",
    "sodium_mg": "sodium",
    "na_mg": "sodium",
    "zinc": "zinc",
    "zinc_mg": "zinc",
    "selenium": "selenium",
    "selenium_mcg": "selenium",
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


def extras_from_portion_micros(
    micros: Any, quantity_g: float
) -> dict[str, float] | None:
    """Convert portion-level AI micros into a per-100g extra_nutrients blob."""
    if quantity_g <= 0:
        return None
    return mapping_from_micros(
        extra_nutrients_to_micros(micros, factor=100.0 / quantity_g)
    )


def extra_nutrients_to_micros(
    extra: Any, *, factor: float = 1.0, validate_units: bool = False
) -> Micros | None:
    """Scale per-100g extras; optionally reject or convert explicit units."""
    if not isinstance(extra, dict) or factor <= 0:
        return None
    candidates: dict[str, tuple[int, float]] = {}
    parsed: dict[str, float] = {}
    for key, raw in extra.items():
        normalized_key = str(key).strip().casefold()
        field = _ALIASES.get(normalized_key)
        if field is None:
            continue
        amount = _amount(raw)
        if amount is None:
            continue
        has_explicit_unit = isinstance(raw, dict) and raw.get("unit") is not None
        if validate_units:
            if isinstance(raw, dict) and not has_explicit_unit:
                continue
            unit = raw.get("unit") if isinstance(raw, dict) else None
            amount = convert_nutrient_amount(amount, unit, field)
            if amount is None:
                continue
        if validate_units:
            is_normalized_row = (
                isinstance(raw, dict) and raw.get("_normalized_row") is True
            )
            rank = (
                int(is_normalized_row) * 100
                + int(has_explicit_unit) * 2
                + int(normalized_key.endswith(_UNIT_SUFFIXES))
            )
            current = candidates.get(field)
            if current is None or rank > current[0]:
                candidates[field] = (rank, amount)
        else:
            parsed[field] = parsed.get(field, 0.0) + amount
    if validate_units:
        parsed = {field: amount for field, (_, amount) in candidates.items()}
    if not parsed:
        return None
    return scale_micros(Micros.from_dict(parsed), factor)


def complete_micros_from_per_100g_portions(
    portions: Iterable[tuple[Any, float]],
) -> Micros | None:
    """Aggregate source-backed nutrients only when each portion is complete."""
    return merge_micros_complete(
        *(
            extra_nutrients_to_micros(
                extra_nutrients,
                factor=grams / 100.0,
                validate_units=True,
            )
            if grams > 0
            else None
            for extra_nutrients, grams in portions
        )
    )


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
    if number < 0 or not math.isfinite(number):
        return None
    return number


_UNIT_SUFFIXES = ("_mcg", "_mg", "_g")
