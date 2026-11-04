"""Supplement nutrients per unit and per dose.

Supplements carry the 19 tracked vitamins and minerals and never calories, so
a dose never touches the weekly budget. Preformed vitamin A (retinol and
retinyl esters) is kept apart from total vitamin A because its upper limit
counts only that form.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields

from src.domain.model.nutrition.micros import Micros

# Micros minus the two limit nutrients that only food carries.
TRACKED_NUTRIENTS: tuple[str, ...] = tuple(
    f.name for f in fields(Micros) if f.name not in ("saturated_fat", "added_sugar")
)
PREFORMED_VITAMIN_A_KEY = "vitamin_a_preformed"
_PRECISION = 4


def _amount(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number")
    try:
        value = float(value)
    except OverflowError:
        raise ValueError(f"{name} is too large") from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite amount of zero or more")
    return float(value)


@dataclass(frozen=True)
class SupplementNutrients:
    """Tracked nutrient amounts in each key's canonical unit.

    A product stores them per unit (one capsule, one ml); an intake stores the
    absolute amount of its dose. ``vitamin_a_preformed`` (µg) is None when the
    label does not say which form its vitamin A takes.
    """

    amounts: Mapping[str, float] = field(default_factory=dict)
    vitamin_a_preformed: float | None = None

    def __post_init__(self) -> None:
        clean = {}
        for key, value in self.amounts.items():
            if key not in TRACKED_NUTRIENTS:
                raise ValueError(f"{key} is not a tracked supplement nutrient")
            clean[key] = _amount(key, value)
        object.__setattr__(self, "amounts", clean)
        if self.vitamin_a_preformed is not None:
            preformed = _amount(PREFORMED_VITAMIN_A_KEY, self.vitamin_a_preformed)
            total = clean.get("vitamin_a")
            if total is None or preformed > total + 10**-_PRECISION:
                raise ValueError("preformed vitamin A cannot exceed vitamin A")
            # Rounding can leave preformed a hair above the total; cap it.
            object.__setattr__(self, "vitamin_a_preformed", min(preformed, total))

    def to_micros(self) -> Micros:
        return Micros(**self.amounts)

    def to_json(self) -> dict[str, float]:
        data = dict(self.amounts)
        if self.vitamin_a_preformed is not None:
            data[PREFORMED_VITAMIN_A_KEY] = self.vitamin_a_preformed
        return data

    @classmethod
    def from_json(cls, data: Mapping[str, object] | None) -> SupplementNutrients:
        """Read stored JSON; keys the app does not track are ignored."""
        data = data or {}
        preformed = data.get(PREFORMED_VITAMIN_A_KEY)
        return cls(
            amounts={
                k: _amount(k, v) for k, v in data.items() if k in TRACKED_NUTRIENTS
            },
            vitamin_a_preformed=None
            if preformed is None
            else _amount(PREFORMED_VITAMIN_A_KEY, preformed),
        )


def dose(per_unit: SupplementNutrients, units: float) -> SupplementNutrients:
    """The absolute nutrients of ``units`` units (2 capsules doubles every key)."""
    units = _amount("units", units)
    if units == 0:
        raise ValueError("a dose needs more than zero units")
    preformed = per_unit.vitamin_a_preformed
    return SupplementNutrients(
        amounts={
            key: round(value * units, _PRECISION)
            for key, value in per_unit.amounts.items()
        },
        vitamin_a_preformed=None
        if preformed is None
        else round(preformed * units, _PRECISION),
    )
