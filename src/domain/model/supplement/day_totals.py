"""A day's supplement doses as the parts the Vi chất view and its warnings need."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from src.domain.model.nutrition.micros import Micros
from src.domain.model.supplement.supplement_intake import SupplementIntake

_PRECISION = 2
# Home draws these as limit gauges; an estimate must never trip one.
_HOME_LIMIT_KEYS = ("sodium",)


def _add(totals: dict[str, float], amounts: dict[str, float]) -> None:
    # "Sodium 0 mg" on a label is not a dose of sodium.
    for key, value in amounts.items():
        if value > 0:
            totals[key] = totals.get(key, 0.0) + value


@dataclass(frozen=True)
class SupplementDayTotals:
    """``micros`` counts every dose; ``estimated_micros`` is the part from
    Ghi nhanh estimates, which never warn.

    ``vitamin_a_preformed`` (µg) sums non-estimated doses. A dose whose label
    names no vitamin A form counts all its vitamin A here: the preformed limit
    is a safety check, so an unknown form errs toward warning.
    """

    micros: dict[str, float] = field(default_factory=dict)
    estimated_micros: dict[str, float] = field(default_factory=dict)
    vitamin_a_preformed: float = 0.0
    doses: int = 0

    def as_micros(self) -> Micros | None:
        return Micros.from_dict(self.micros) if self.micros else None

    def home_micros(self) -> dict[str, float]:
        """What the day's highlights count: every dose, except estimates of a
        nutrient Home shows as a limit gauge (sodium)."""
        home = dict(self.micros)
        for key in _HOME_LIMIT_KEYS:
            rest = home.get(key, 0.0) - self.estimated_micros.get(key, 0.0)
            if rest > 0:
                home[key] = rest
            else:
                home.pop(key, None)
        return home

    def home_highlight_micros(self) -> Micros | None:
        home = self.home_micros()
        return Micros.from_dict(home) if home else None

    def to_fields(self) -> dict[str, object]:
        """The per-day Trends fields."""
        return {
            "supplement_micros": _rounded(self.micros),
            "supplement_estimated_micros": _rounded(self.estimated_micros),
            "supplement_vitamin_a_preformed": round(
                self.vitamin_a_preformed, _PRECISION
            ),
            "supplement_doses": self.doses,
        }


def _rounded(values: dict[str, float]) -> dict[str, float]:
    return {key: round(value, _PRECISION) for key, value in sorted(values.items())}


def supplement_day_totals(doses: Iterable[SupplementIntake]) -> SupplementDayTotals:
    micros: dict[str, float] = {}
    estimated: dict[str, float] = {}
    preformed = 0.0
    count = 0
    for dose in doses:
        count += 1
        amounts = dict(dose.nutrients.amounts)
        _add(micros, amounts)
        if dose.estimated:
            _add(estimated, amounts)
            continue
        known = dose.nutrients.vitamin_a_preformed
        preformed += known if known is not None else amounts.get("vitamin_a", 0.0)
    return SupplementDayTotals(micros, estimated, preformed, count)
