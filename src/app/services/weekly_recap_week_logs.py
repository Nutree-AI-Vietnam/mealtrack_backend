"""Read what one user logged that their weekly recap needs.

Logs from before sign-up are left out.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from uuid import UUID

from src.app.services.weekly_recap_activity_stats import recap_goal
from src.app.services.weekly_recap_meal_stats import FoodMeal, food_meals
from src.domain.model.hydration.hydration_entry import HydrationEntry
from src.domain.model.meal_projection import MealProjection
from src.domain.model.movement.movement_entry import MovementEntry
from src.domain.model.notification.notification_preferences import (
    DEFAULT_MEAL_REMINDER_MINUTES,
    NotificationPreferences,
)
from src.domain.model.weekly_recap import WeeklyRecap
from src.domain.model.weight.weight_entry import WeightEntry
from src.domain.services.weekly_recap import monday_of, week_days
from src.domain.utils.timezone_utils import ensure_utc, get_zone_info

_MEAL_LIMIT = 2_000


@dataclass(frozen=True)
class WeekLogs:
    """What one user logged that their recap for one week reads."""

    week_start: date
    tz: tzinfo
    created: date  # sign-up day, local
    goal: str
    profile: Any
    meals: tuple[FoodMeal, ...]
    movements: tuple[MovementEntry, ...]  # sign-up through the week's end
    drinks: tuple[HydrationEntry, ...]
    weights_this_week: tuple[WeightEntry, ...]
    weights_last_week: tuple[WeightEntry, ...]
    log_dates: tuple[date, ...]
    case_counts: Mapping[str, int]
    reminders: Mapping[str, int]  # meal reminder times, minutes from midnight
    first_name: str | None = None
    last_recap: WeeklyRecap | None = None  # the week before, if it was told


def sign_up_day(created_at: datetime | None, tz: tzinfo, fallback: date) -> date:
    """The local day the account was created; ``fallback`` when unknown."""
    created = ensure_utc(created_at)
    return created.astimezone(tz).date() if created is not None else fallback


async def load_week_logs(
    uow: Any,
    user_id: str,
    week_start: date,
    tz_name: str,
    created_at: datetime | None,
    min_week_days: int,
    first_name: str | None = None,
) -> WeekLogs | None:
    """Read the week's logs, and the recap told for the week before.

    None when the account existed for fewer than ``min_week_days`` days of
    the week, or no food was logged in it.
    """
    tz = get_zone_info(tz_name)
    log_dates = tuple(
        await uow.meals.get_dates_with_meals(user_id, user_timezone=tz_name)
    )
    created = sign_up_day(created_at, tz, fallback=min(log_dates, default=week_start))
    if week_days(week_start, created) < min_week_days:
        return None
    first = max(week_start, created)
    week_end = week_start + timedelta(days=6)
    meals = await uow.meals.find_by_date_range(
        user_id,
        week_start,
        week_end,
        limit=_MEAL_LIMIT,
        user_timezone=tz_name,
        projection=MealProjection.MACROS_ONLY,
    )
    food = tuple(
        meal
        for meal in food_meals(meals, week_start, tz)
        if week_start + timedelta(days=meal.day) >= first
    )
    if not food:
        return None
    movements = await uow.movement_entries.find_by_user_and_logged_range(
        user_id,
        _midnight_utc(monday_of(created), tz),
        _midnight_utc(week_end + timedelta(days=1), tz),
    )
    weights = await uow.weight_entries.find_by_recorded_range(
        user_id,
        _midnight_utc(week_start - timedelta(days=7), tz),
        _midnight_utc(week_end + timedelta(days=1), tz),
    )
    drinks = await uow.hydration_entries.find_by_date_range(
        user_id, week_start, week_end, user_timezone=tz_name
    )
    profile = await uow.users.get_profile(UUID(user_id))
    prefs = await uow.notifications.find_notification_preferences_by_user(user_id)
    return WeekLogs(
        week_start=week_start,
        tz=tz,
        created=created,
        goal=recap_goal(profile.fitness_goal if profile is not None else None),
        profile=profile,
        meals=food,
        movements=_since(movements, "logged_at", created, tz),
        drinks=_since(drinks, "logged_at", first, tz),
        weights_this_week=_since(weights, "recorded_at", week_start, tz),
        weights_last_week=tuple(
            entry for entry in weights if _local_day(entry.recorded_at, tz) < week_start
        ),
        log_dates=log_dates,
        case_counts=await uow.weekly_recaps.count_cases_before(user_id, week_start),
        reminders=_reminders(prefs),
        first_name=first_name,
        last_recap=await uow.weekly_recaps.get(user_id, week_start - timedelta(days=7)),
    )


def _reminders(prefs: NotificationPreferences | None) -> dict[str, int]:
    """Main meals' reminder times; the defaults for meals without one."""
    reminders = dict(DEFAULT_MEAL_REMINDER_MINUTES)
    if prefs is not None:
        for meal in reminders:
            minutes = prefs.get_meal_reminder_time(meal)
            if minutes is not None:
                reminders[meal] = minutes
    return reminders


def _since(entries: Any, attr: str, first: date, tz: tzinfo) -> tuple[Any, ...]:
    """Entries whose ``attr`` time falls on ``first`` or later, locally."""
    return tuple(
        entry for entry in entries if _local_day(getattr(entry, attr), tz) >= first
    )


def _midnight_utc(day: date, tz: tzinfo) -> datetime:
    return datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)


def _local_day(moment: datetime | None, tz: tzinfo) -> date:
    utc = ensure_utc(moment)
    return utc.astimezone(tz).date() if utc is not None else date.min
