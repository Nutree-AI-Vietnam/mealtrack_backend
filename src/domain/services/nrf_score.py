"""Daily nutrient-density score inspired by NRF9.3, using FDA Daily Values.

Not a certified NRF index. Protein and fiber always come from macros.
Coverage counts only vitamins, minerals, and limit nutrients that were logged.
"""

from __future__ import annotations

from src.domain.model.nutrition.micros import Micros

NRF_MIN_COVERAGE = 4

_ENCOURAGE_MICRO_DVS: tuple[tuple[str, float], ...] = (
    ("vitamin_a", 900.0),
    ("vitamin_c", 90.0),
    ("vitamin_e", 15.0),
    ("calcium", 1300.0),
    ("iron", 18.0),
    ("magnesium", 420.0),
    ("potassium", 4700.0),
)
_LIMIT_DVS: tuple[tuple[str, float], ...] = (
    ("saturated_fat", 20.0),
    ("added_sugar", 50.0),
    ("sodium", 2300.0),
)
_COVERAGE_FIELDS: tuple[str, ...] = tuple(
    name for name, _ in _ENCOURAGE_MICRO_DVS
) + tuple(name for name, _ in _LIMIT_DVS)


def nrf_coverage(micros: Micros | None) -> int:
    if micros is None:
        return 0
    return sum(
        1 for name in _COVERAGE_FIELDS if getattr(micros, name) is not None
    )


def nrf_quality(
    protein_g: float,
    fiber_g: float,
    micros: Micros | None,
) -> float:
    encourage = [
        _capped_pct(protein_g, 50.0),
        _capped_pct(fiber_g, 25.0),
    ]
    for name, dv in _ENCOURAGE_MICRO_DVS:
        value = None if micros is None else getattr(micros, name)
        if value is not None:
            encourage.append(_capped_pct(value, dv))
    limits: list[float] = []
    for name, dv in _LIMIT_DVS:
        value = None if micros is None else getattr(micros, name)
        if value is not None:
            limits.append(_capped_pct(value, dv))
    raw = _mean(encourage) - (_mean(limits) if limits else 0.0)
    return round((raw + 100.0) / 2.0, 1)


def nrf_progress_fields(
    protein_g: float,
    fiber_g: float,
    micros: Micros | None,
) -> dict[str, float | int]:
    coverage = nrf_coverage(micros)
    return {
        "nrf_quality": (
            nrf_quality(protein_g, fiber_g, micros)
            if coverage >= NRF_MIN_COVERAGE
            else 0.0
        ),
        "nrf_coverage": coverage,
    }


def _capped_pct(actual: float, daily_value: float) -> float:
    if daily_value <= 0:
        return 0.0
    return min(100.0, max(0.0, actual) / daily_value * 100.0)


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)
