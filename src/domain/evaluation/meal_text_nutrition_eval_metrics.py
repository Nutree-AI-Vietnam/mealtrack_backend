from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from src.domain.model.nutrition.extra_nutrients import food_item_effective_micros

from .meal_text_nutrition_eval_models import (
    TEXT_MACROS,
    TEXT_MICRO_FLOORS,
    TEXT_MICRO_UNITS,
)


def sum_item_macros(items: list[Any]) -> dict[str, float]:
    result = dict.fromkeys(TEXT_MACROS, 0.0)
    keys = {
        "protein_g": "protein",
        "carbs_g": "carbs",
        "fat_g": "fat",
        "fiber_g": "fiber",
        "sugar_g": "sugar",
    }
    for item in items:
        for name, attr in keys.items():
            result[name] += float(getattr(item, attr, 0.0) or 0.0)
    return result


def sum_raw_macros(items: list[dict[str, Any]]) -> dict[str, float]:
    values = dict.fromkeys(TEXT_MACROS, 0.0)
    aliases = {
        "protein_g": "protein",
        "carbs_g": "carbs",
        "fat_g": "fat",
        "fiber_g": "fiber",
        "sugar_g": "sugar",
    }
    for item in items:
        macros = item.get("macros") if isinstance(item.get("macros"), dict) else item
        for name, alias in aliases.items():
            values[name] += float(macros.get(name, macros.get(alias, 0.0)) or 0.0)
    return values


def sum_complete_micros(items: list[dict[str, Any]]) -> dict[str, float | None]:
    totals: dict[str, float | None] = {}
    for name in TEXT_MICRO_UNITS:
        values = [(item.get("micros") or {}).get(name) for item in items]
        totals[name] = (
            sum(float(value) for value in values)
            if items and all(value is not None for value in values)
            else None
        )
    return totals


def any_micro_present(items: list[dict[str, Any]], nutrient: str) -> bool:
    return any((item.get("micros") or {}).get(nutrient) is not None for item in items)


def sum_effective_micros(items: list[Any]) -> dict[str, float | None]:
    portions = [food_item_effective_micros(_effective_item(item)) for item in items]
    totals: dict[str, float | None] = {}
    for name in TEXT_MICRO_UNITS:
        values = [getattr(portion, name, None) for portion in portions]
        totals[name] = (
            sum(float(value) for value in values)
            if portions and all(value is not None for value in values)
            else None
        )
    return totals


def any_effective_micro_present(items: list[Any], nutrient: str) -> bool:
    return any(
        getattr(food_item_effective_micros(_effective_item(item)), nutrient, None)
        is not None
        for item in items
    )


def _effective_item(item: Any) -> Any:
    """Normalize API serving-unit models for the domain portion scaler."""
    allowed_units = []
    for option in getattr(item, "allowed_units", None) or []:
        if isinstance(option, dict):
            allowed_units.append(option)
        elif callable(getattr(option, "model_dump", None)):
            allowed_units.append(option.model_dump())
        else:
            allowed_units.append(
                {
                    "unit": getattr(option, "unit", None),
                    "gram_weight": getattr(option, "gram_weight", None),
                }
            )
    return SimpleNamespace(
        micros=getattr(item, "micros", None),
        source_snapshot=getattr(item, "source_snapshot", None),
        quantity=getattr(item, "quantity", None),
        unit=getattr(item, "unit", None),
        allowed_units=allowed_units,
    )


def score_micro_references(
    references: dict[str, dict[str, Any]],
    actual: dict[str, float | None],
    has_value,
) -> tuple[dict[str, float], dict[str, float], dict[str, int]]:
    coverage: dict[str, float] = {}
    errors: dict[str, float] = {}
    unknown: dict[str, int] = {}
    for name, reference in references.items():
        validate_micro(name, reference)
        status = reference["status"]
        if status == "masked":
            continue
        if status == "unknown":
            if has_value(name):
                unknown[name] = 1
            continue
        value = actual.get(name)
        coverage[name] = float(value is not None)
        if value is None:
            errors[name] = 1.0
        else:
            reference_value = reference["value"]
            errors[name] = interval_error(value, reference_value) / max(
                midpoint(reference_value), TEXT_MICRO_FLOORS[name]
            )
    return coverage, errors, unknown


def validate_micro(name: str, reference: dict[str, Any]) -> None:
    if name not in TEXT_MICRO_UNITS:
        raise ValueError(f"unsupported micronutrient: {name}")
    if reference.get("status") not in {"known", "unknown", "masked"}:
        raise ValueError(f"{name} reference status must be known, unknown, or masked")
    if reference.get("unit") != TEXT_MICRO_UNITS[name]:
        raise ValueError(f"{name} unit must be {TEXT_MICRO_UNITS[name]}")
    if reference["status"] == "known" and "value" not in reference:
        raise ValueError(f"known {name} reference requires a value or interval")


def interval_error(actual: float, expected: Any) -> float:
    if isinstance(expected, (tuple, list)):
        low, high = float(expected[0]), float(expected[1])
        return max(low - actual, actual - high, 0.0)
    return abs(actual - float(expected))


def midpoint(value: Any) -> float:
    return sum(value) / 2 if isinstance(value, (tuple, list)) else float(value)
