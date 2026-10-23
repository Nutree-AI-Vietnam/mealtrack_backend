"""Weekly recap day-by-day numbers, read from the progress summary rows.

The rows are the ones the app's progress screen shows, so the recap's
calories, protein, water, and targets match what the user saw each day.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

_ML_PER_L = 1000.0


@dataclass(frozen=True)
class DayStats:
    """Per-day numbers (Monday first) and the week's targets."""

    kcal: tuple[float, ...]
    protein: tuple[float, ...]
    water: tuple[float, ...]  # litres
    budgets: tuple[float, ...]
    budget: int
    protein_target: float
    fiber_avg: float
    fiber_target: float
    water_target: float  # litres
    cheat_days: tuple[int, ...]
    fiber: tuple[float, ...] = ()  # grams, food days only


def day_stats(
    rows: Iterable[Mapping[str, Any]], week_start: date, food_days: Collection[int]
) -> DayStats | None:
    """The week's day numbers, or None when the week has no calorie target.

    Calories and protein count only on ``food_days`` (days with a meal
    eaten), so a drink alone does not make a day logged. Days missing from
    the rows (before sign-up) take the week's average calorie target.
    """
    by_day = _rows_by_day(rows, week_start)
    targets = [_num(row, "target_calories") for row in by_day.values()]
    targets = [target for target in targets if target > 0]
    if not targets:
        return None
    budget = round(sum(targets) / len(targets))
    if budget <= 0:
        return None
    last = by_day[max(by_day)]
    fiber = [_num(by_day[day], "fiber_g") for day in sorted(food_days) if day in by_day]
    fiber_targets = [_num(row, "fiber_target_g") for row in by_day.values()]
    return DayStats(
        kcal=_per_day(by_day, "calories", food_days),
        protein=_per_day(by_day, "protein_g", food_days),
        water=tuple(
            _num(by_day[day], "hydration_ml") / _ML_PER_L if day in by_day else 0.0
            for day in range(7)
        ),
        budgets=tuple(_day_budget(by_day.get(day), budget) for day in range(7)),
        budget=budget,
        protein_target=_num(last, "protein_target_g"),
        fiber_avg=sum(fiber) / len(fiber) if fiber else 0.0,
        fiber_target=float(round(sum(fiber_targets) / len(fiber_targets))),
        water_target=_num(last, "hydration_goal_ml") / _ML_PER_L,
        cheat_days=tuple(
            day for day in sorted(by_day) if by_day[day].get("is_cheat_day")
        ),
        fiber=_per_day(by_day, "fiber_g", food_days),
    )


def _rows_by_day(
    rows: Iterable[Mapping[str, Any]], week_start: date
) -> dict[int, Mapping[str, Any]]:
    by_day: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        try:
            day = (date.fromisoformat(str(row["date"])) - week_start).days
        except (KeyError, ValueError):
            continue
        if 0 <= day < 7:
            by_day[day] = row
    return by_day


def _day_budget(row: Mapping[str, Any] | None, budget: int) -> float:
    """The day's calorie target, or the week's average when it has none."""
    target = _num(row, "target_calories") if row is not None else 0.0
    return target if target > 0 else float(budget)


def _per_day(
    by_day: Mapping[int, Mapping[str, Any]], key: str, food_days: Collection[int]
) -> tuple[float, ...]:
    return tuple(
        _num(by_day[day], key) if day in by_day and day in food_days else 0.0
        for day in range(7)
    )


def _num(row: Mapping[str, Any], key: str) -> float:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)
