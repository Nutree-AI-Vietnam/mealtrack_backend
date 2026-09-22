"""Canonical quantity handling for catalog and grocery projections."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CanonicalIngredientQuantity:
    """A quantity safe to aggregate without inventing conversions."""

    amount: Decimal
    unit: str
    dimension: str
    confidence: str


_WEIGHT_UNITS = {
    "mg": (Decimal("0.001"), "g"),
    "g": (Decimal("1"), "g"),
    "gram": (Decimal("1"), "g"),
    "grams": (Decimal("1"), "g"),
    "kg": (Decimal("1000"), "g"),
    "kilogram": (Decimal("1000"), "g"),
}
_VOLUME_UNITS = {
    "ml": (Decimal("1"), "ml"),
    "milliliter": (Decimal("1"), "ml"),
    "milliliters": (Decimal("1"), "ml"),
    "l": (Decimal("1000"), "ml"),
    "liter": (Decimal("1000"), "ml"),
    "liters": (Decimal("1000"), "ml"),
}
_COUNT_UNITS = {
    "piece": "piece",
    "pieces": "piece",
    "unit": "unit",
    "units": "unit",
    "serving": "serving",
    "slice": "slice",
    "slices": "slice",
    "quả": "quả",
    "củ": "củ",
    "bó": "bó",
    "gói": "gói",
    "chai": "chai",
}


def normalize_ingredient_quantity(
    amount: Decimal | float | int, unit: str
) -> CanonicalIngredientQuantity:
    """Normalize only units with deterministic conversions.

    Unknown and count-based units remain separate dimensions. This prevents
    the grocery projection from inventing weights such as ``1 tomato = 120g``.
    """

    raw_unit = " ".join(str(unit).strip().casefold().split()) or "unknown"
    value = Decimal(str(amount))
    if raw_unit in _WEIGHT_UNITS:
        multiplier, canonical_unit = _WEIGHT_UNITS[raw_unit]
        return CanonicalIngredientQuantity(
            amount=value * multiplier,
            unit=canonical_unit,
            dimension="weight",
            confidence="exact",
        )
    if raw_unit in _VOLUME_UNITS:
        multiplier, canonical_unit = _VOLUME_UNITS[raw_unit]
        return CanonicalIngredientQuantity(
            amount=value * multiplier,
            unit=canonical_unit,
            dimension="volume",
            confidence="exact",
        )
    if raw_unit in _COUNT_UNITS:
        canonical_unit = _COUNT_UNITS[raw_unit]
        return CanonicalIngredientQuantity(
            amount=value,
            unit=canonical_unit,
            dimension=f"count:{canonical_unit}",
            confidence="unconverted",
        )
    return CanonicalIngredientQuantity(
        amount=value,
        unit=raw_unit,
        dimension=f"raw:{raw_unit}",
        confidence="unknown",
    )
