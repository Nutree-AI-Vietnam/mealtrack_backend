"""Combine meal rows with hydration entries that are not already meals.

Caloric drinks can be stored only as hydration entries. A legacy hydration
meal (coffee, water) must not cause those entry-only drinks to be dropped,
and an entry that points at a meal already in the total must not be added
twice.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from src.domain.model.meal import MealStatus
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.micros_ops import merge_micros
from src.domain.utils.timezone_utils import ensure_utc, get_zone_info


def represented_meal_ids(meals: list[Any]) -> set[str]:
    """Meal ids whose nutrition is already included in a day total."""
    represented: set[str] = set()
    for meal in meals:
        if getattr(meal, "status", None) == MealStatus.INACTIVE:
            continue
        meal_id = getattr(meal, "meal_id", None)
        if meal_id:
            represented.add(str(meal_id))
    return represented


def unrepresented_hydration_entries(
    entries: list[Any], meals: list[Any]
) -> list[Any]:
    """Hydration rows that are not a second copy of a counted meal."""
    represented = represented_meal_ids(meals)
    pending: list[Any] = []
    for entry in entries:
        legacy_meal_id = getattr(entry, "legacy_meal_id", None)
        if legacy_meal_id and str(legacy_meal_id) in represented:
            continue
        pending.append(entry)
    return pending


def entry_macros(entry: Any) -> Macros:
    return Macros(
        protein=float(getattr(entry, "protein_g", 0) or 0),
        carbs=float(getattr(entry, "carbs_g", 0) or 0),
        fat=float(getattr(entry, "fat_g", 0) or 0),
        fiber=float(getattr(entry, "fiber_g", 0) or 0),
        sugar=float(getattr(entry, "sugar_g", 0) or 0),
    )


def entry_local_date(entry: Any, user_timezone: str | None) -> date | None:
    logged_at = getattr(entry, "logged_at", None)
    if logged_at is None:
        return None
    aware = ensure_utc(logged_at)
    if user_timezone:
        return aware.astimezone(get_zone_info(user_timezone)).date()
    return aware.date()


def entry_in_consumed_window(
    local_day: date,
    *,
    end_date: date | None = None,
    exclude_date: date | None = None,
    exclude_dates: set[date] | None = None,
) -> bool:
    if end_date is not None and local_day > end_date:
        return False
    if exclude_date is not None and local_day == exclude_date:
        return False
    if exclude_dates and local_day in exclude_dates:
        return False
    return True


def add_unrepresented_drink_totals(
    consumed: dict[str, float],
    meals: list[Any],
    entries: list[Any],
    *,
    end_date: date | None = None,
    exclude_date: date | None = None,
    exclude_dates: list[date] | None = None,
    user_timezone: str | None = None,
) -> dict[str, float]:
    """Return consumed totals plus entry-only drinks in the same window."""
    excluded = set(exclude_dates) if exclude_dates else set()
    protein = float(consumed.get("protein", 0) or 0)
    carbs = float(consumed.get("carbs", 0) or 0)
    fat = float(consumed.get("fat", 0) or 0)
    calories = float(consumed.get("calories", 0) or 0)
    for entry in unrepresented_hydration_entries(entries, meals):
        local_day = entry_local_date(entry, user_timezone)
        if local_day is None:
            continue
        if not entry_in_consumed_window(
            local_day,
            end_date=end_date,
            exclude_date=exclude_date,
            exclude_dates=excluded,
        ):
            continue
        macros = entry_macros(entry)
        protein += macros.protein
        carbs += macros.carbs
        fat += macros.fat
        calories += macros.total_calories
    return {
        "protein": protein,
        "carbs": carbs,
        "fat": fat,
        "calories": calories,
    }


def orphan_drink_day_totals(
    entries: list[Any],
    meals_by_day: dict[date, list[Any]],
    user_timezone: str | None,
) -> tuple[dict[date, Macros], dict[date, Any]]:
    """Per-day macros and micros for drinks that are not already meals."""
    meals = [meal for day_meals in meals_by_day.values() for meal in day_meals]
    macros_by_day: dict[date, Macros] = {}
    micros_by_day: dict[date, Any] = {}
    for entry in unrepresented_hydration_entries(entries, meals):
        local_day = entry_local_date(entry, user_timezone)
        if local_day is None:
            continue
        macros = entry_macros(entry)
        previous = macros_by_day.get(local_day)
        if previous is None:
            macros_by_day[local_day] = macros
        else:
            macros_by_day[local_day] = Macros(
                protein=previous.protein + macros.protein,
                carbs=previous.carbs + macros.carbs,
                fat=previous.fat + macros.fat,
                fiber=previous.fiber + macros.fiber,
                sugar=previous.sugar + macros.sugar,
            )
        entry_micros = getattr(entry, "micros", None)
        if entry_micros is not None:
            micros_by_day[local_day] = merge_micros(
                micros_by_day.get(local_day), entry_micros
            )
    return macros_by_day, micros_by_day


def intake_watermark(
    meal_at: datetime | None,
    hydration_at: datetime | None,
    movement_at: datetime | None,
) -> str:
    """Fingerprint of the newest meal, drink, and workout touch on a day.

    Each log type is kept separate so deleting a drink still changes the
    fingerprint when a later meal exists.
    """
    return "|".join(
        (
            _utc_stamp(meal_at),
            _utc_stamp(hydration_at),
            _utc_stamp(movement_at),
        )
    )


def _utc_stamp(value: datetime | None) -> str:
    if value is None:
        return ""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()
