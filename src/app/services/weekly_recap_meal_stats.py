"""Weekly recap numbers that come from the week's food meals.

Drinks are left out: the recap's meals are what was eaten. Meals logged for
a past day are stamped at local noon, so their time says nothing about when
they were eaten and they are left out of the timing numbers (log hour, late
snacks, pre-workout carbs).
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

from src.domain.constants.meal_constants import MealDefaults
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.weekly_recap import DishCount, MissedMeal
from src.domain.services.meal_calorie_service import effective_meal_calories
from src.domain.services.meal_type_determination_service import (
    determine_meal_type_from_timestamp,
)
from src.domain.utils.timezone_utils import ensure_utc

MEAL_ORDER = ("breakfast", "lunch", "dinner", "snack")
MAIN_MEALS = MEAL_ORDER[:3]
PHOTO_SOURCES = frozenset({"scanner", "food_label"})
_HYDRATION = "hydration"
_BACKDATED_AT = time(12, 0)
_NIGHT_ENDS_HOUR = 4
_PLACEHOLDER_NAMES = frozenset(
    {"unknown dish", MealDefaults.UNNAMED_FOOD_NAME.casefold()}
)
_MEALS_PER_DAY_CAP = 3
_TOP_DISHES = 3
_MIN_DISH_REPEATS = 2
_MIN_HOUR_MEALS = 2
_MIN_MISSED_DAYS = 3


@dataclass(frozen=True)
class FoodMeal:
    """One food meal of the week, in the user's local time."""

    day: int  # 0 = Monday .. 6 = Sunday
    logged_at: datetime
    meal_type: str
    name: str
    kcal: float
    carbs: float
    fiber: float
    source: str | None = None

    @property
    def backdated(self) -> bool:
        """Logged for a past day, so stamped at noon rather than eaten then."""
        return self.logged_at.time() == _BACKDATED_AT


def food_meals(meals: Iterable[Meal], week_start: date, tz: tzinfo) -> list[FoodMeal]:
    """The week's analysed food meals, oldest first."""
    out: list[FoodMeal] = []
    for meal in meals:
        created_at = ensure_utc(meal.created_at)
        if created_at is None or not _is_food(meal) or meal.nutrition is None:
            continue
        logged_at = created_at.astimezone(tz)
        day = (logged_at.date() - week_start).days
        if not 0 <= day < 7:
            continue
        macros = meal.nutrition.effective_macros
        out.append(
            FoodMeal(
                day=day,
                logged_at=logged_at,
                meal_type=_meal_type(meal.meal_type, logged_at),
                name=" ".join((meal.dish_name or "").split()),
                kcal=effective_meal_calories(meal),
                carbs=float(macros.carbs or 0.0),
                fiber=float(macros.fiber or 0.0),
                source=meal.source,
            )
        )
    return sorted(out, key=lambda meal: meal.logged_at)


def meals_by_day(meals: Sequence[FoodMeal]) -> tuple[int, ...]:
    """Meals per day, counting at most three a day."""
    counts = Counter(meal.day for meal in meals)
    return tuple(min(_MEALS_PER_DAY_CAP, counts[day]) for day in range(7))


def late_snacks(
    meals: Sequence[FoodMeal], late_hour: int
) -> tuple[tuple[int, ...], int]:
    """Nights with food after ``late_hour`` and the average kcal per night.

    Food logged after midnight, before 4 a.m., counts for the night before.
    """
    nights: dict[int, float] = {}
    for meal in _timed(meals):
        hour = meal.logged_at.hour
        if hour >= late_hour:
            night = meal.day
        elif hour < _NIGHT_ENDS_HOUR:
            night = meal.day - 1
        else:
            continue
        if night >= 0:
            nights[night] = nights.get(night, 0.0) + meal.kcal
    if not nights:
        return (), 0
    return tuple(sorted(nights)), round(sum(nights.values()) / len(nights))


def lunch_no_veg_days(meals: Sequence[FoodMeal], veg_fiber_g: float) -> int:
    """Days whose lunch had less than ``veg_fiber_g`` of fiber."""
    fiber: dict[int, float] = {}
    for meal in meals:
        if meal.meal_type == "lunch":
            fiber[meal.day] = fiber.get(meal.day, 0.0) + meal.fiber
    return sum(1 for grams in fiber.values() if grams < veg_fiber_g)


def dish_stats(meals: Sequence[FoodMeal]) -> tuple[int, tuple[DishCount, ...]]:
    """How many different dishes, and the (up to 3) most repeated ones.

    A dish shows its latest name and the median kcal of its servings.
    """
    groups: dict[str, list[FoodMeal]] = {}
    for meal in meals:
        key = meal.name.casefold()
        if key and key not in _PLACEHOLDER_NAMES:
            groups.setdefault(key, []).append(meal)
    repeats = sorted(
        (item for item in groups.items() if len(item[1]) >= _MIN_DISH_REPEATS),
        key=lambda item: (-len(item[1]), item[0]),
    )
    top = tuple(
        DishCount(
            name=group[-1].name,
            count=len(group),
            kcal=round(statistics.median(meal.kcal for meal in group)),
        )
        for _, group in repeats[:_TOP_DISHES]
    )
    return len(groups), top


def common_log_hour(meals: Sequence[FoodMeal]) -> tuple[int | None, str | None]:
    """The hour most meals were logged in, and the meal usually logged then.

    Needs two meals in that hour; ties go to the earlier hour.
    """
    timed = _timed(meals)
    hours = Counter(meal.logged_at.hour for meal in timed)
    if not hours:
        return None, None
    hour, count = min(hours.items(), key=lambda item: (-item[1], item[0]))
    if count < _MIN_HOUR_MEALS:
        return None, None
    types = Counter(meal.meal_type for meal in timed if meal.logged_at.hour == hour)
    meal_type, _ = min(
        types.items(), key=lambda item: (-item[1], MEAL_ORDER.index(item[0]))
    )
    return hour, meal_type


def missed_meal(
    meals: Sequence[FoodMeal], reminders: Mapping[str, int]
) -> MissedMeal | None:
    """The main meal most often missing on days with food logged.

    None unless one is missing on at least three days. ``reminders`` gives
    each meal's reminder time, in minutes from midnight.
    """
    days = {meal.day for meal in meals}
    eaten = {(meal.day, meal.meal_type) for meal in meals}
    worst: MissedMeal | None = None
    for meal_type in MAIN_MEALS:
        missing = sum(1 for day in days if (day, meal_type) not in eaten)
        if missing >= _MIN_MISSED_DAYS and (worst is None or missing > worst.days):
            worst = MissedMeal(
                meal_type=meal_type,
                days=missing,
                remind_at=reminders.get(meal_type),
            )
    return worst


def photo_share(meals: Sequence[FoodMeal]) -> float:
    """Share of meals logged from a photo; 1.0 when none says how."""
    sourced = [meal for meal in meals if meal.source]
    if not sourced:
        return 1.0
    photos = sum(1 for meal in sourced if meal.source in PHOTO_SOURCES)
    return photos / len(sourced)


def pre_workout_carbs(
    meals: Sequence[FoodMeal],
    workout_starts: Iterable[tuple[int, datetime]],
    window_min: int,
) -> float | None:
    """Average carbs eaten in the ``window_min`` minutes before a workout.

    Only workouts on days with food logged count; None without any.
    """
    timed = _timed(meals)
    food_days = {meal.day for meal in meals}
    window = timedelta(minutes=window_min)
    totals = [
        sum(meal.carbs for meal in timed if start - window <= meal.logged_at < start)
        for day, start in workout_starts
        if day in food_days
    ]
    return sum(totals) / len(totals) if totals else None


def _is_food(meal: Meal) -> bool:
    return (
        meal.status in (MealStatus.READY, MealStatus.ENRICHING)
        and meal.meal_type != _HYDRATION
        and meal.source != _HYDRATION
    )


def _meal_type(stored: str | None, logged_at: datetime) -> str:
    if stored is not None and stored in MEAL_ORDER:
        return stored
    return determine_meal_type_from_timestamp(logged_at)


def _timed(meals: Sequence[FoodMeal]) -> list[FoodMeal]:
    return [meal for meal in meals if not meal.backdated]
