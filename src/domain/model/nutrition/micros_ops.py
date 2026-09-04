"""Merge, scale, and serialize Micros without mutating frozen instances."""

from __future__ import annotations

from typing import Any

from src.domain.model.nutrition.micros import Micros

_FIELDS = tuple(Micros.__dataclass_fields__)


def is_empty(micros: Micros | None) -> bool:
    if micros is None:
        return True
    return all(getattr(micros, name) is None for name in _FIELDS)


def micros_from_mapping(data: Any) -> Micros | None:
    if not isinstance(data, dict) or not data:
        return None
    valid = set(_FIELDS)
    parsed: dict[str, float] = {}
    for key, raw in data.items():
        if key not in valid:
            continue
        value = _as_non_negative_float(raw)
        if value is not None:
            parsed[key] = value
    if not parsed:
        return None
    return Micros.from_dict(parsed)


def mapping_from_micros(micros: Micros | None) -> dict[str, float] | None:
    if is_empty(micros):
        return None
    return micros.to_dict()  # type: ignore[union-attr]


def merge_micros(*parts: Micros | None) -> Micros | None:
    combined: dict[str, float] = {}
    for part in parts:
        if is_empty(part):
            continue
        for name in _FIELDS:
            value = getattr(part, name)
            if value is None:
                continue
            combined[name] = combined.get(name, 0.0) + value
    if not combined:
        return None
    return Micros.from_dict(combined)


def scale_micros(micros: Micros | None, factor: float) -> Micros | None:
    if is_empty(micros):
        return None
    if factor == 1:
        return micros
    if factor <= 0:
        return None
    scaled = {
        name: getattr(micros, name) * factor
        for name in _FIELDS
        if getattr(micros, name) is not None
    }
    return Micros.from_dict(scaled) if scaled else None


def _as_non_negative_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number
