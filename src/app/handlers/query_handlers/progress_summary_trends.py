"""Fields that GET /v1/progress/summary adds only with include=trends."""

from __future__ import annotations

from datetime import date
from typing import Any

from src.app.handlers.query_handlers.progress_summary_support import (
    local_days_utc_bounds,
)
from src.domain.model.meal import MealStatus
from src.domain.model.movement.movement_load import daily_movement_load
from src.domain.model.nutrition.extra_nutrients import merge_meal_micros
from src.domain.model.nutrition.micros import Micros
from src.domain.model.nutrition.micros_ops import merge_micros
from src.domain.services.meal_nrf_fields import nutrition_nrf_fields
from src.domain.services.micronutrient_dri_targets import micronutrient_daily_goals
from src.domain.utils.timezone_utils import ensure_utc

# The app's score bands (Tốt >= 70, Khá 50-69, Cần cải thiện < 50).
_GOOD_QUALITY = 70.0
_FAIR_QUALITY = 50.0
_SCORED_STATUSES = (MealStatus.READY, MealStatus.ENRICHING)


async def fetch_movement_entries_by_day(
    uow: Any,
    user_id: str,
    start: date,
    end: date,
    user_tz_str: str,
    user_tz: Any,
) -> dict[date, list[Any]]:
    """Every movement entry in the window, bucketed by the user's local day."""
    start_utc, end_utc = local_days_utc_bounds(start, end, user_tz_str)
    entries = await uow.movement_entries.find_by_user_and_logged_range(
        user_id, start_utc, end_utc
    )
    by_day: dict[date, list[Any]] = {}
    for entry in entries:
        logged = ensure_utc(entry.logged_at)
        if logged is None:
            continue
        by_day.setdefault(logged.astimezone(user_tz).date(), []).append(entry)
    return by_day


def included_burn_by_day(entries_by_day: dict[date, list[Any]]) -> dict[date, float]:
    """Calories that count toward the balance, as the default path sums them."""
    return {
        day: sum(
            float(entry.kcal_burned or 0.0)
            for entry in entries
            if entry.include_in_balance
        )
        for day, entries in entries_by_day.items()
    }


def trends_day_fields(
    meals: list[Any], drink_micros: Micros | None, movement_entries: list[Any]
) -> dict[str, Any]:
    """Trends fields for one day; micros here include the food items' own."""
    day_micros: Micros | None = None
    micro_meal_counts: dict[str, int] = {}
    quality_counts = {"good": 0, "fair": 0, "low": 0}
    quality_by_type: dict[str, list[float]] = {}
    for meal in meals:
        if not meal.nutrition or meal.status not in _SCORED_STATUSES:
            continue
        reported = merge_meal_micros(
            getattr(meal.nutrition, "micros", None),
            getattr(meal.nutrition, "food_items", None),
        )
        day_micros = merge_micros(day_micros, reported)
        if meal.meal_type == "hydration":
            continue
        for key in reported.to_dict() if reported else {}:
            micro_meal_counts[key] = micro_meal_counts.get(key, 0) + 1
        quality = nutrition_nrf_fields(meal.nutrition, reported)["nrf_quality"]
        if quality is None:
            continue
        quality_counts[_quality_band(float(quality))] += 1
        if meal.meal_type:
            quality_by_type.setdefault(meal.meal_type, []).append(float(quality))
    day_micros = merge_micros(day_micros, drink_micros)
    load = daily_movement_load(movement_entries)
    return {
        "micros": {
            key: round(value, 2)
            for key, value in (day_micros.to_dict() if day_micros else {}).items()
        },
        "micro_meal_counts": micro_meal_counts,
        "meal_quality_counts": quality_counts,
        "quality_by_meal_type": {
            meal_type: round(sum(scores) / len(scores), 1)
            for meal_type, scores in quality_by_type.items()
        },
        "movement_minutes": load.minutes,
        "movement_sessions": load.sessions,
        "movement_load": load.load,
        "movement_kinds": load.kinds,
    }


def trends_goal_fields(
    profile: Any, plan_calories: float, revision: int | None
) -> dict[str, Any]:
    """The person's goals and upper limits, once per response.

    ``goals_revision`` is the profile target revision they were built from, so
    the app can tell its cached goals are out of date after a My Plan edit.
    """
    personal = micronutrient_daily_goals(
        gender=getattr(profile, "gender", None),
        age=getattr(profile, "age", None),
        plan_calories=plan_calories,
        dietary_preferences=getattr(profile, "dietary_preferences", None),
    )
    return {
        "goals_revision": revision,
        "micro_goals": personal.goals,
        "micro_upper_limits": {
            key: {"value": limit.value, "scope": limit.scope.value}
            for key, limit in personal.upper_limits.items()
        },
    }


def _quality_band(quality: float) -> str:
    if quality >= _GOOD_QUALITY:
        return "good"
    if quality >= _FAIR_QUALITY:
        return "fair"
    return "low"
