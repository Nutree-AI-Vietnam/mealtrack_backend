"""Canonical units for nutrient values stored in ``Micros``."""

from __future__ import annotations

import math

_CANONICAL_UNITS = {
    "vitamin_a": "mcg",
    "vitamin_c": "mg",
    "vitamin_d": "mcg",
    "vitamin_e": "mg",
    "vitamin_k": "mcg",
    "thiamin": "mg",
    "riboflavin": "mg",
    "niacin": "mg",
    "vitamin_b6": "mg",
    "vitamin_b12": "mcg",
    "folate": "mcg",
    "calcium": "mg",
    "iron": "mg",
    "magnesium": "mg",
    "phosphorus": "mg",
    "potassium": "mg",
    "sodium": "mg",
    "zinc": "mg",
    "selenium": "mcg",
    "saturated_fat": "g",
    "added_sugar": "g",
}

_UNIT_ALIASES = {
    "microgram": "mcg",
    "micrograms": "mcg",
    "ug": "mcg",
    "milligram": "mg",
    "milligrams": "mg",
    "gram": "g",
    "grams": "g",
}

_CONVERSIONS = {
    "mcg": {"mcg": 1.0, "mg": 1000.0, "g": 1_000_000.0},
    "mg": {"mcg": 0.001, "mg": 1.0, "g": 1000.0},
    "g": {"mcg": 0.000001, "mg": 0.001, "g": 1.0},
}


def convert_nutrient_amount(
    amount: float, unit: str | None, field: str
) -> float | None:
    """Convert a known mass unit to the canonical unit, rejecting unknown units."""
    if not math.isfinite(amount):
        return None
    if unit is None:
        return amount
    normalized = str(unit).strip().casefold().replace("μ", "u").replace("µ", "u")
    normalized = _UNIT_ALIASES.get(normalized, normalized)
    if normalized not in _CONVERSIONS:
        return None
    converted = amount * _CONVERSIONS[_CANONICAL_UNITS[field]][normalized]
    return converted if math.isfinite(converted) else None
