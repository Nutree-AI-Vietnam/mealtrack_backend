"""Range-load helpers for the progress summary query handler."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.domain.cache.cache_keys import CacheKeys
from src.domain.ports.cache_port import CachePort
from src.domain.services.weekly_budget_service import WeeklyBudgetService
from src.domain.utils.timezone_utils import (
    ensure_utc,
    get_user_monday,
    get_zone_info,
)

logger = logging.getLogger(__name__)


async def fetch_movement_by_day(
    uow: Any,
    user_id: str,
    start: date,
    end: date,
    user_tz_str: str,
    user_tz: Any,
) -> dict[date, float]:
    tz = get_zone_info(user_tz_str)
    start_utc = datetime.combine(start, datetime.min.time(), tzinfo=tz).astimezone(UTC)
    end_utc = (
        datetime.combine(end, datetime.min.time(), tzinfo=tz) + timedelta(days=1)
    ).astimezone(UTC)
    included = await uow.movement_entries.fetch_included_kcal_for_range(
        user_id, start_utc, end_utc
    )
    by_day: dict[date, float] = {}
    for logged_at, kcal in included:
        local = ensure_utc(logged_at)
        if local is None:
            continue
        day = local.astimezone(user_tz).date()
        by_day[day] = by_day.get(day, 0.0) + kcal
    return by_day


async def resolve_today_live_target(
    uow: Any,
    user_id: str,
    today: date,
    start: date,
    end: date,
    user_tz_str: str,
    base_cal: float,
    protein_t: float,
    bmr: float,
) -> float | None:
    if today < start or today > end:
        return None
    week_start = get_user_monday(today, user_id)
    weekly = await uow.weekly_budgets.find_by_user_and_week(user_id, week_start)
    if weekly is None:
        return None
    auto_adjust = WeeklyBudgetService.auto_adjust_enabled(
        await uow.users.get_weekly_auto_adjust(user_id)
    )
    effective = await WeeklyBudgetService.get_effective_adjusted_daily_async(
        uow=uow,
        user_id=user_id,
        week_start=week_start,
        target_date=today,
        weekly_budget=weekly,
        base_daily_cal=base_cal,
        base_daily_protein=protein_t,
        base_daily_carbs=weekly.target_carbs / 7,
        base_daily_fat=weekly.target_fat / 7,
        bmr=bmr,
        user_timezone=user_tz_str,
        auto_adjust=auto_adjust,
    )
    return effective.adjusted.calories


def resolve_day_target(
    day: date,
    today: date,
    live_cal: float | None,
    snapshots: dict[date, float],
    base_cal: float,
) -> tuple[str, float]:
    if day == today and live_cal is not None:
        return "adjusted_live", live_cal
    if day in snapshots:
        return "snapshot", snapshots[day]
    return "base", base_cal


async def load_tdee_targets(
    user_id: str, cache_service: CachePort | None
) -> tuple[float, float, float, float, int | None, float]:
    """(calories, protein, carbs, fat, revision, bmr)."""
    try:
        from src.app.handlers.query_handlers.get_user_tdee_query_handler import (
            GetUserTdeeQueryHandler,
        )
        from src.app.queries.tdee import GetUserTdeeQuery

        result = await GetUserTdeeQueryHandler(cache_service=cache_service).handle(
            GetUserTdeeQuery(user_id=user_id)
        )
        macros = result.get("macros", {})
        return (
            result.get("target_calories", 2000.0),
            macros.get("protein", 70.0),
            macros.get("carbs", 200.0),
            macros.get("fat", 70.0),
            result.get("profile_target_revision"),
            result.get("bmr", 1800.0),
        )
    except Exception as exc:
        logger.warning("Could not fetch TDEE for %s: %s", user_id, exc)
        return 2000.0, 70.0, 200.0, 70.0, None, 1800.0


async def read_summary_cache(
    cache: CachePort | None,
    user_id: str,
    start: date,
    end: date,
    revision: int | None,
) -> dict[str, Any] | None:
    if cache is None or revision is None:
        return None
    key, _ = CacheKeys.progress_summary(user_id, start, end)
    cached = await cache.get_json(key)
    if cached and cached.get("target_revision") == revision:
        return cached
    return None


async def write_summary_cache(
    cache: CachePort | None,
    user_id: str,
    start: date,
    end: date,
    payload: dict[str, Any],
) -> None:
    if cache is None:
        return
    key, ttl = CacheKeys.progress_summary(user_id, start, end)
    await cache.set_json(key, payload, ttl, revision_field="target_revision")


def local_meal_date(created_at: Any, user_tz: Any) -> date | None:
    if created_at is None:
        return None
    aware = ensure_utc(created_at)
    return aware.astimezone(user_tz).date() if aware else None


def hydration_goal_ml(uow_profile: Any) -> int:
    from src.domain.services.hydration_goal_service import resolve_hydration_goal_ml

    return resolve_hydration_goal_ml(uow_profile) if uow_profile else 2000
