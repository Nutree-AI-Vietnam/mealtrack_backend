from __future__ import annotations

import re
from typing import Any

from .prompt_eval_models import MICRO_ERROR_FLOORS, MICRO_UNITS


def score_foods(expected: tuple[dict[str, Any], ...], actual: list[Any]):
    if not expected:
        return 0, 0, 0, []
    unmatched = list(expected)
    grams: list[float] = []
    true_positive = false_positive = 0
    for food in actual:
        name = normalize_name(getattr(food, "name", ""))
        match = next(
            (
                item
                for item in unmatched
                if name in {normalize_name(alias) for alias in item.get("aliases", [])}
            ),
            None,
        )
        if match is None:
            false_positive += 1
            continue
        unmatched.remove(match)
        true_positive += 1
        grams.append(interval_error(float(food.quantity), match["quantity_g"]))
    return true_positive, false_positive, len(unmatched), grams


def validate_micro_reference(nutrient: str, reference: dict[str, Any]) -> None:
    if nutrient not in MICRO_UNITS:
        raise ValueError(f"unsupported micronutrient: {nutrient}")
    if reference.get("status") not in {"known", "unknown", "masked"}:
        raise ValueError(
            f"{nutrient} reference status must be known, unknown, or masked"
        )
    if reference.get("unit") != MICRO_UNITS[nutrient]:
        raise ValueError(f"{nutrient} unit must be {MICRO_UNITS[nutrient]}")
    if reference["status"] == "known" and "value" not in reference:
        raise ValueError(f"known {nutrient} reference requires a value or interval")


def interval_error(actual: float, expected: float | tuple[float, float]) -> float:
    if isinstance(expected, (tuple, list)):
        low, high = float(expected[0]), float(expected[1])
        return max(low - actual, actual - high, 0.0)
    return abs(actual - float(expected))


def midpoint(value: float | tuple[float, float]) -> float:
    return sum(value) / 2 if isinstance(value, (tuple, list)) else float(value)


def meal_macros(macros: Any) -> dict[str, float]:
    return {
        "protein_g": float(macros.protein),
        "carbs_g": float(macros.carbs),
        "fat_g": float(macros.fat),
        "fiber_g": float(macros.fiber),
        "sugar_g": float(macros.sugar),
    }


def whole_meal_micros(structured: dict[str, Any]) -> dict[str, float | None]:
    foods = structured.get("foods")
    if not isinstance(foods, list) or not foods:
        return dict.fromkeys(MICRO_UNITS)
    totals: dict[str, float | None] = {}
    for name in MICRO_UNITS:
        values = []
        for food in foods:
            micros = food.get("micros") if isinstance(food, dict) else None
            values.append(micros.get(name) if isinstance(micros, dict) else None)
        totals[name] = (
            sum(float(value) for value in values)
            if all(value is not None for value in values)
            else None
        )
    return totals


def score_micro_references(
    structured: dict[str, Any],
    references: dict[str, dict[str, Any]],
    counts: dict[str, int],
    hits: dict[str, int],
    errors: dict[str, list[float]],
    unknown: dict[str, int],
) -> None:
    actual_micros = whole_meal_micros(structured)
    for nutrient, reference in references.items():
        validate_micro_reference(nutrient, reference)
        status = reference["status"]
        if status == "masked":
            continue
        actual = actual_micros.get(nutrient)
        if status == "unknown":
            if actual is not None:
                unknown[nutrient] = unknown.get(nutrient, 0) + 1
            continue
        counts[nutrient] = counts.get(nutrient, 0) + 1
        if actual is None:
            errors.setdefault(nutrient, []).append(1.0)
            continue
        hits[nutrient] = hits.get(nutrient, 0) + 1
        interval = reference["value"]
        denominator = max(midpoint(interval), MICRO_ERROR_FLOORS[nutrient])
        errors.setdefault(nutrient, []).append(
            interval_error(actual, interval) / denominator
        )


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", " ", value.casefold())).strip()


def append_optional(values: list[float], value: float | None) -> None:
    if value is not None:
        values.append(float(value))


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile_value)))
    return ordered[index]
