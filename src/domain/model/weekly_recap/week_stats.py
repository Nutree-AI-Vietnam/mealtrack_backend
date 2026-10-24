"""Weekly recap input: one user's Monday-to-Sunday week, already aggregated.

Per-day tuples always hold seven values, index 0 = Monday. Days before the
account existed are zeros; ``week_days`` says how many days count.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Workout:
    """One logged workout. ``day`` is 0 (Monday) .. 6 (Sunday)."""

    day: int
    minutes: int
    kcal: int
    strength: bool = False


@dataclass(frozen=True)
class DishCount:
    """A dish logged more than once, with its usual kcal per portion."""

    name: str
    count: int
    kcal: int


@dataclass(frozen=True)
class MissedMeal:
    """Main meal most often left unlogged (breakfast, lunch or dinner)."""

    meal_type: str
    days: int
    remind_at: int | None = None  # the user's reminder for it, minutes from 0:00


@dataclass(frozen=True)
class PeakMeal:
    """The biggest meal logged on one day. ``day`` is 0 (Monday) .. 6."""

    day: int
    name: str
    kcal: int


@dataclass(frozen=True)
class LastWeek:
    """The week before, as this week's recap compares with it.

    Read from that week's stored recap. ``water_avg`` is None when no water
    was logged; ``plan`` holds the issues that week's plan showed. The
    ``*_days``/``days_logged`` coverage counts say how many days fed each
    count, so a week with fewer logged days never reads as progress.
    """

    week_days: int
    protein_avg: float
    fiber_avg: float
    water_avg: float | None
    days_on_budget: int
    workouts: int
    late_snack_nights: int
    sugary_drinks: int
    weekend_over_days: int
    rest_under_days: int
    days_logged: int
    water_days: int
    weekend_days_counted: int
    rest_days_counted: int
    pre_workout_carbs: float | None = None
    plan: tuple[str, ...] = ()


@dataclass(frozen=True)
class WeekStats:
    """Everything the recap engine reads about one week."""

    week_start: date
    week_no: int
    account_days: int
    goal: str  # cut | bulk | recomp
    budget: int
    protein_target: float
    meals_by_day: tuple[int, ...]
    kcal_by_day: tuple[float, ...]
    protein_by_day: tuple[float, ...]
    water_by_day: tuple[float, ...]  # litres
    fiber_avg: float
    streak: int
    budget_by_day: tuple[float, ...] | None = None
    fiber_target: float = 25.0
    water_target: float = 2.5
    carbs_target: float = 60.0
    week_days: int = 7
    gap_before: int = 0
    lunch_no_veg_days: int = 0
    workouts: tuple[Workout, ...] = ()
    late_snack_days: tuple[int, ...] = ()
    late_snack_kcal: int = 0
    streak_is_record: bool = False
    workouts_is_record: bool = False
    photo_share: float = 1.0
    unique_dishes: int = 0
    top_dishes: tuple[DishCount, ...] = ()
    common_log_hour: int | None = None
    common_meal: str | None = None  # breakfast | lunch | dinner | snack
    missed_meal: MissedMeal | None = None
    sugary_drinks: int = 0
    sugary_kcal: int = 0
    weekend_delta: float = 0.0
    under_days: tuple[int, ...] = ()
    training_day_kcal: float = 0.0
    pre_workout_carbs: float | None = None
    progress_pct: float | None = None
    progress_delta: float | None = None
    perfect_weeks_before: int = 0
    had_first_week: bool = False
    cheat_days: tuple[int, ...] = ()
    first_name: str | None = None
    diet: str | None = None  # vegetarian | vegan | keto; None for no restriction
    fiber_by_day: tuple[float, ...] = ()  # empty in snapshots stored before it
    low_protein_meal: str | None = None  # breakfast | lunch | dinner
    top_sugary_drink: str | None = None
    weekend_peaks: tuple[PeakMeal, ...] = ()  # the biggest meal of Fri, Sat, Sun
    last_week: LastWeek | None = None
