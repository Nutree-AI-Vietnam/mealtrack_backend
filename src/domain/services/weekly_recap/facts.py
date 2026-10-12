"""Counts and averages derived from WeekStats that several insights share.

Day indexes run from Monday (0) to Sunday (6). For a week the account only
partly existed, ``first_day`` is the sign-up weekday; the days before it
hold zeros, and ``week_days`` is used as the denominator.

A day is on budget when it stays within the tolerance above its target. A
bulk day also has to reach ``floor_ratio`` of its target: bulking means
eating enough, so a day well under target misses it too.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from src.domain.model.weekly_recap import WeekStats
from src.domain.services.weekly_recap.config import RecapConfig
from src.domain.services.weekly_recap.format import js_round

_WEEKEND = (4, 5, 6)


@dataclass(frozen=True)
class WeekFacts:
    """Shared numbers for one week. See the module docstring for indexes."""

    week_days: int
    first_day: int
    targets: tuple[float, ...]
    floor_ratio: float
    logged: tuple[bool, ...]
    meals_logged: int
    meals_expected: int
    days_logged: int
    days_on_budget: int
    over_days: tuple[int, ...]
    over_kcal: int
    kcal_avg: float
    protein_avg: float
    protein_days_hit: int
    water_avg: float
    water_hit_days: tuple[int, ...]
    water_tracked: bool
    workout_days: tuple[int, ...]
    strength_days: tuple[int, ...]
    strength_count: int
    weekend_over_days: tuple[int, ...]
    weekend_over_kcal: float
    rest_under_days: tuple[int, ...]
    rest_shortfall: float
    training_days: tuple[int, ...]
    training_fueled: int
    low_data_threshold: int

    @property
    def low_data(self) -> bool:
        """Too few meals for a full story."""
        return self.meals_logged < self.low_data_threshold

    @property
    def floors(self) -> tuple[float, ...]:
        """Fewest kcal each day can eat and still be on budget."""
        return tuple(target * self.floor_ratio for target in self.targets)

    @property
    def water_days_hit(self) -> int:
        """Days the water target was reached."""
        return len(self.water_hit_days)


def week_facts(stats: WeekStats, config: RecapConfig) -> WeekFacts:
    """Derive the shared numbers for ``stats``."""
    wd = stats.week_days
    kcal = stats.kcal_by_day
    targets = tuple(stats.budget_by_day or (float(stats.budget),) * 7)
    logged = tuple(meals > 0 for meals in stats.meals_by_day)
    cheat = set(stats.cheat_days)
    counted = [day for day in range(7) if logged[day] and day not in cheat]
    floor_ratio = config.under_ratio if stats.goal == "bulk" else 0.0
    floors = [target * floor_ratio for target in targets]
    ceiling = [target * config.on_budget_tolerance for target in targets]
    over = tuple(
        day for day in range(7) if kcal[day] > ceiling[day] and day not in cheat
    )
    weekend_over = tuple(
        day for day in _WEEKEND if day in counted and kcal[day] > targets[day]
    )
    workout_days = tuple(sorted({workout.day for workout in stats.workouts}))
    strength = [workout for workout in stats.workouts if workout.strength]
    rest_under = tuple(day for day in stats.under_days if day not in workout_days)
    training = tuple(day for day in workout_days if day in counted)
    return WeekFacts(
        week_days=wd,
        first_day=7 - wd,
        targets=targets,
        floor_ratio=floor_ratio,
        logged=logged,
        meals_logged=sum(stats.meals_by_day),
        meals_expected=3 * wd,
        days_logged=sum(logged),
        days_on_budget=sum(
            1
            for day in range(7)
            if 0 < kcal[day] and floors[day] <= kcal[day] <= ceiling[day]
        ),
        over_days=over,
        over_kcal=js_round(kcal[over[0]] - targets[over[0]]) if over else 0,
        kcal_avg=_positive_mean(kcal),
        protein_avg=_positive_mean(stats.protein_by_day),
        protein_days_hit=_days_at_least(stats.protein_by_day, stats.protein_target),
        water_avg=sum(stats.water_by_day) / wd if wd else 0.0,
        water_hit_days=tuple(
            day
            for day, litres in enumerate(stats.water_by_day)
            if stats.water_target > 0 and litres >= stats.water_target
        ),
        water_tracked=any(litres > 0 for litres in stats.water_by_day),
        workout_days=workout_days,
        strength_days=tuple(sorted({workout.day for workout in strength})),
        strength_count=len(strength),
        weekend_over_days=weekend_over,
        weekend_over_kcal=_mean([kcal[day] - targets[day] for day in weekend_over]),
        rest_under_days=rest_under,
        rest_shortfall=_mean([targets[day] - kcal[day] for day in rest_under]),
        training_days=training,
        training_fueled=sum(
            1
            for day in training
            if kcal[day] >= targets[day] * config.training_fuel_ratio
        ),
        low_data_threshold=math.ceil(config.low_data_meals * wd / 7),
    )


def _days_at_least(values: Sequence[float], target: float) -> int:
    if target <= 0:
        return 0
    return sum(1 for value in values if value >= target)


def _positive_mean(values: Sequence[float]) -> float:
    return _mean([value for value in values if value > 0])


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0
