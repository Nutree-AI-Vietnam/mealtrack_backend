"""Validation helpers for reviewed food and nutrition references."""

from __future__ import annotations

from typing import Any

from src.domain.services.meal_analysis.prompt_eval_loop import MICRO_UNITS


def _validate_expected_food(case_id: str, food: Any) -> dict[str, Any]:
    if not isinstance(food, dict):
        raise ValueError(f"{case_id}: expected foods must be objects")
    aliases = food.get("aliases")
    if (
        not isinstance(aliases, list)
        or not aliases
        or any(not str(alias).strip() for alias in aliases)
    ):
        raise ValueError(f"{case_id}: each expected food requires reviewed aliases")
    quantity = food.get("quantity_g")
    if isinstance(quantity, list):
        quantity = _interval(quantity, f"{case_id}.food.quantity_g")
    elif not isinstance(quantity, (int, float)) or quantity <= 0:
        raise ValueError(
            f"{case_id}: expected food quantity_g must be positive or an interval"
        )
    return {"aliases": [str(alias) for alias in aliases], "quantity_g": quantity}


def _validate_macro_refs(raw: Any, case_id: str) -> dict[str, Any]:
    allowed = {"protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError(f"{case_id}: expected_macros contains invalid fields")
    return {
        name: _interval_or_value(value, f"{case_id}.{name}")
        for name, value in raw.items()
    }


def _validate_micro_refs(raw: Any, case_id: str) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or set(raw) - set(MICRO_UNITS):
        raise ValueError(f"{case_id}: expected_micros contains invalid fields")
    result: dict[str, dict[str, Any]] = {}
    for name, reference in raw.items():
        if not isinstance(reference, dict) or reference.get("status") not in {
            "known",
            "unknown",
            "masked",
        }:
            raise ValueError(
                f"{case_id}: {name} status must be known, unknown, or masked"
            )
        if reference.get("unit") != MICRO_UNITS[name]:
            raise ValueError(f"{case_id}: {name} unit must be {MICRO_UNITS[name]}")
        item = {"status": reference["status"], "unit": reference["unit"]}
        if reference["status"] == "known":
            item["value"] = _interval_or_value(
                reference.get("value"), f"{case_id}.{name}.value"
            )
        elif "value" in reference:
            raise ValueError(
                f"{case_id}: {reference['status']} {name} references cannot contain values"
            )
        result[name] = item
    return result


def _interval(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must be a two-number reference interval")
    low, high = float(value[0]), float(value[1])
    if low < 0 or high < low:
        raise ValueError(f"{label} is not a valid non-negative interval")
    return low, high


def _interval_or_value(value: Any, label: str) -> float | tuple[float, float]:
    if isinstance(value, (list, tuple)):
        return _interval(value, label)
    number = float(value)
    if number < 0:
        raise ValueError(f"{label} must be non-negative")
    return number
