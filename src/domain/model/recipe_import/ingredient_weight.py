"""Weigh an ingredient line for nutrition.

Weights convert exactly. Volumes convert through food density for liquids
the density table knows; a dry food by volume (1 cup flour) keeps the gram
estimate, since flour is not 240 g a cup. Anything else (2 cloves, 1 can,
a pinch) uses the estimate too, because a count unit has no fixed weight.
"""

from __future__ import annotations

import math

from src.domain.constants.food_density import FOOD_DENSITY_MAP, get_density
from src.domain.model.recipe_import.limits import MAX_INGREDIENT_GRAMS
from src.domain.services.nutrition_calculation_service import UNIT_TRANSLATION

_MASS_GRAMS: dict[str, float] = {
    "mg": 0.001,
    "g": 1.0,
    "gr": 1.0,
    "gram": 1.0,
    "kg": 1000.0,
    "oz": 28.35,
    "ounce": 28.35,
    "lb": 453.6,
    "lbs": 453.6,
    "pound": 453.6,
}
_VOLUME_ML: dict[str, float] = {
    "ml": 1.0,
    "cl": 10.0,
    "dl": 100.0,
    "l": 1000.0,
    "liter": 1000.0,
    "litre": 1000.0,
    "tsp": 5.0,
    "teaspoon": 5.0,
    "tbsp": 15.0,
    "tablespoon": 15.0,
    "cup": 240.0,
    "fl oz": 29.57,
    "pint": 473.0,
    "quart": 946.0,
}


def canonical_unit(unit: str | None) -> str | None:
    """Lower-case English unit, plural folded, translated where known."""
    if not unit:
        return None
    key = " ".join(unit.lower().replace(".", " ").split())
    if not key:
        return None
    key = UNIT_TRANSLATION.get(key, key)
    if key in _MASS_GRAMS or key in _VOLUME_ML:
        return key
    if key.endswith("es") and key[:-2] in _MASS_GRAMS | _VOLUME_ML:
        return key[:-2]
    if key.endswith("s") and key[:-1] in _MASS_GRAMS | _VOLUME_ML:
        return key[:-1]
    return key


def is_measured_unit(unit: str | None) -> bool:
    """Whether the unit is a weight or a volume the backend can convert."""
    key = canonical_unit(unit)
    return key is not None and (key in _MASS_GRAMS or key in _VOLUME_ML)


def ingredient_grams(
    *,
    english_name: str,
    quantity: float | None,
    unit: str | None,
    estimated_grams: float | None,
) -> float | None:
    """Grams of one ingredient line, or None when it cannot be weighed."""
    key = canonical_unit(unit)
    if _positive(quantity) and key is not None:
        assert quantity is not None
        if key in _MASS_GRAMS:
            return _bounded(quantity * _MASS_GRAMS[key])
        if key in _VOLUME_ML and (
            _has_known_density(english_name) or not _positive(estimated_grams)
        ):
            return _bounded(quantity * _VOLUME_ML[key] * get_density(english_name))
    if _positive(estimated_grams):
        assert estimated_grams is not None
        return _bounded(estimated_grams)
    return None


def _has_known_density(english_name: str) -> bool:
    name = english_name.lower()
    return any(key in name for key in FOOD_DENSITY_MAP)


def _positive(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0


def _bounded(grams: float) -> float | None:
    if not math.isfinite(grams) or grams <= 0 or grams > MAX_INGREDIENT_GRAMS:
        return None
    return round(grams, 1)
