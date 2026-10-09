"""Validation for calories, macro intervals, and micro reference states."""

from __future__ import annotations

from typing import Any

from src.domain.evaluation.meal_text_nutrition_eval_models import (
    TEXT_MACROS,
    TEXT_MICRO_UNITS,
)


def _validated_reference(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{label} must be a two-number reference interval")
    low, high = float(value[0]), float(value[1])
    if low < 0 or high < low:
        raise ValueError(f"{label} must be a valid non-negative interval")
    return low, high


def _validated_micro_references(raw: Any, case_id: str) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or set(raw) - set(TEXT_MICRO_UNITS):
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
        if reference.get("unit") != TEXT_MICRO_UNITS[name]:
            raise ValueError(f"{case_id}: {name} unit must be {TEXT_MICRO_UNITS[name]}")
        normalized = {"status": reference["status"], "unit": reference["unit"]}
        if reference["status"] == "known":
            normalized["value"] = _validated_reference_or_value(
                reference.get("value"), f"{case_id}.{name}.value"
            )
        elif "value" in reference:
            raise ValueError(
                f"{case_id}: {reference['status']} {name} references cannot contain values"
            )
        result[name] = normalized
    return result


def _validated_macro_references(raw: Any, case_id: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - set(TEXT_MACROS):
        raise ValueError(f"{case_id}: expected_macros contains invalid fields")
    result: dict[str, Any] = {}
    for name, value in raw.items():
        if isinstance(value, list):
            result[name] = _validated_reference(value, f"{case_id}.{name}")
            continue
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{case_id}.{name} must be a value or interval") from exc
        if number < 0:
            raise ValueError(f"{case_id}.{name} must be non-negative")
        result[name] = number
    return result


def _validated_reference_or_value(
    value: Any, label: str
) -> float | tuple[float, float]:
    if isinstance(value, list):
        return _validated_reference(value, label)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a non-negative number or interval") from exc
    if number < 0:
        raise ValueError(f"{label} must be non-negative")
    return number
