"""Personal daily goals and upper limits for the 22 tracked nutrients.

Goals follow Vietnam's 2016 recommended intakes by sex, age and diet
(``vn_nutrient_tables``); upper limits come from ``upper_limits``. Sodium,
added sugar and saturated fat are limits to stay under, so their goal is the
limit itself.

The Nutrition Score keeps its own fixed FDA Daily Values in ``nrf_score.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.domain.model.nutrition import vn_nutrient_tables as vn
from src.domain.model.nutrition.upper_limits import UpperLimit, upper_limits_for

_DEFAULT_AGE = 30
_DEFAULT_PLAN_CALORIES = 2000.0
_KCAL_PER_G_FAT = 9.0
_PLANT_BASED_DIETS = frozenset({"vegetarian", "vegan"})


@dataclass(frozen=True)
class MicronutrientGoals:
    """Goals keyed like ``Micros`` (21 keys) plus fiber, and the upper limits."""

    goals: dict[str, float]
    fiber_g: float
    upper_limits: dict[str, UpperLimit]


def micronutrient_daily_goals(
    *,
    gender: str | None,
    age: int | None,
    plan_calories: float,
    dietary_preferences: Iterable[str] | None = None,
) -> MicronutrientGoals:
    years = age if age and age > 0 else _DEFAULT_AGE
    male = is_male(gender)
    goals = {
        key: vn.lookup(table, years, male) for key, table in vn.GOAL_TABLES.items()
    }
    if is_plant_based(dietary_preferences):
        goals["iron"] = vn.lookup(vn.IRON_PLANT_BASED_MG, years, male)
    goals["sodium"] = vn.SODIUM_LIMIT_MG
    goals["added_sugar"] = vn.ADDED_SUGAR_LIMIT_G
    goals["saturated_fat"] = saturated_fat_limit_g(plan_calories)
    return MicronutrientGoals(
        goals=goals,
        fiber_g=fiber_goal_g(gender, age),
        upper_limits=upper_limits_for(years, male),
    )


def fiber_goal_g(gender: str | None, age: int | None) -> float:
    years = age if age and age > 0 else _DEFAULT_AGE
    return vn.lookup(vn.FIBER_G, years, is_male(gender))


def saturated_fat_limit_g(plan_calories: float) -> float:
    kcal = plan_calories if plan_calories > 0 else _DEFAULT_PLAN_CALORIES
    return round(vn.SATURATED_FAT_ENERGY_FRACTION * kcal / _KCAL_PER_G_FAT, 1)


def is_male(gender: str | None) -> bool:
    return (gender or "").strip().lower() in {"male", "m"}


def is_plant_based(dietary_preferences: Iterable[str] | None) -> bool:
    return any(
        str(value).strip().casefold() in _PLANT_BASED_DIETS
        for value in (dietary_preferences or ())
    )
