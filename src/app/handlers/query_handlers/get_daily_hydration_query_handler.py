"""
GetDailyHydrationQueryHandler — cache-aside handler for daily hydration summary.
"""

import logging
from datetime import date, datetime, timedelta
from uuid import UUID

from src.app.events.base import EventHandler, handles
from src.app.queries.hydration.get_daily_hydration_query import GetDailyHydrationQuery
from src.domain.cache.cache_keys import CacheKeys
from src.domain.model.hydration import HydrationEntry
from src.domain.model.meal import Meal
from src.domain.ports.cache_port import CachePort
from src.domain.services.hydration_catalog_service import (
    localized_name_for_catalog_name,
)
from src.domain.services.hydration_goal_service import resolve_hydration_goal_ml
from src.domain.utils.timezone_utils import (
    format_iso_utc,
    get_zone_info,
    resolve_user_timezone_async,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


def _compute_streak(totals: dict[date, int], today: date, goal_ml: int) -> int:
    """Count consecutive days ending on/before today where consumed_ml >= goal_ml."""
    yesterday = today - timedelta(days=1)
    most_recent: date | None = None
    for days_back in range(31):
        d = today - timedelta(days=days_back)
        if totals.get(d, 0) >= goal_ml:
            most_recent = d
            break
    if most_recent is None or most_recent < yesterday:
        return 0
    streak = 0
    current = most_recent
    while current >= today - timedelta(days=30):
        if totals.get(current, 0) >= goal_ml:
            streak += 1
            current -= timedelta(days=1)
        else:
            break
    return streak


def _build_entry_dict(meal: Meal, language: str = "en") -> dict:
    kcal = round(
        (
            (meal.nutrition.macros.carbs * 4 + meal.nutrition.macros.fat * 9)
            if meal.nutrition
            else 0.0
        ),
        1,
    )
    return {
        "id": meal.meal_id,
        "drink_name": localized_name_for_catalog_name(meal.dish_name, language),
        "emoji": meal.emoji or "💧",
        "volume_ml": meal.quantity or 0,
        "credited_ml": meal.quantity or 0,
        "kcal": kcal,
        "calories": kcal,
        "source": meal.source or "hydration",
        "meal_id": meal.meal_id,
        "logged_at": format_iso_utc(meal.created_at),
    }


def _build_hydration_entry_dict(entry: HydrationEntry, language: str = "en") -> dict:
    kcal = round(entry.calories, 1)
    return {
        "id": entry.id,
        "drink_id": entry.drink_id,
        "drink_name": localized_name_for_catalog_name(
            entry.drink_name_snapshot, language
        ),
        "emoji": entry.emoji_snapshot or "💧",
        "volume_ml": entry.volume_ml,
        "credited_ml": entry.credited_ml,
        "kcal": kcal,
        "calories": kcal,
        "source": entry.source,
        "meal_id": entry.legacy_meal_id,
        "logged_at": format_iso_utc(entry.logged_at),
    }


def _merge_normalized_with_legacy_totals(
    normalized: dict[date, int],
    legacy: dict[date, int],
) -> dict[date, int]:
    merged = dict(normalized)
    for day, total in legacy.items():
        if day not in merged:
            merged[day] = total
    return merged


@handles(GetDailyHydrationQuery)
class GetDailyHydrationQueryHandler(EventHandler[GetDailyHydrationQuery, dict]):
    """Handler for fetching a user's daily hydration summary with cache-aside."""

    def __init__(self, cache_service: CachePort | None = None):
        self.cache_service = cache_service

    async def handle(self, query: GetDailyHydrationQuery) -> dict:
        async with AsyncUnitOfWork() as uow:
            user_tz_str = await resolve_user_timezone_async(
                query.user_id, uow, query.header_timezone
            )
            user_tz = get_zone_info(user_tz_str)
            target_date: date = query.target_date or datetime.now(user_tz).date()

            cache_key, ttl = CacheKeys.daily_hydration(
                query.user_id, target_date, query.language
            )
            if self.cache_service:
                cached = await self.cache_service.get_json(cache_key)
                if cached is not None:
                    return cached

            hydration_entries = await uow.hydration_entries.find_by_date(
                date_obj=target_date,
                user_id=query.user_id,
                user_timezone=user_tz_str,
            )
            legacy_entries = []
            if not hydration_entries:
                legacy_entries = await uow.meals.find_by_date(
                    date_obj=target_date,
                    user_id=query.user_id,
                    user_timezone=user_tz_str,
                    meal_type="hydration",
                )

            legacy_consumed_ml = 0
            if hydration_entries:
                consumed_ml = sum(entry.credited_ml for entry in hydration_entries)
            else:
                legacy_consumed_ml = await uow.meals.sum_hydration_ml_for_date(
                    target_date, query.user_id, user_tz_str
                )
                consumed_ml = legacy_consumed_ml

            entries_payload = (
                [
                    _build_hydration_entry_dict(entry, query.language)
                    for entry in hydration_entries
                ]
                if hydration_entries
                else [_build_entry_dict(e, query.language) for e in legacy_entries]
            )

            normalized_daily_totals = await uow.hydration_entries.sum_ml_by_date_range(
                query.user_id,
                target_date - timedelta(days=30),
                target_date,
                user_tz_str,
            )
            legacy_daily_totals = await uow.meals.sum_hydration_ml_by_date_range(
                query.user_id,
                target_date - timedelta(days=30),
                target_date,
                user_tz_str,
            )
            daily_totals = _merge_normalized_with_legacy_totals(
                normalized_daily_totals, legacy_daily_totals
            )

            if hydration_entries:
                daily_totals[target_date] = consumed_ml
            elif legacy_consumed_ml:
                daily_totals[target_date] = legacy_consumed_ml

            goal_ml = 2000
            try:
                user_profile = await uow.users.get_profile(UUID(query.user_id))
                if user_profile:
                    goal_ml = resolve_hydration_goal_ml(user_profile)
            except Exception:
                logger.debug(
                    "Could not fetch user profile for water goal; defaulting to 2000 ml",
                    exc_info=True,
                )

            percentage = round((consumed_ml / goal_ml * 100), 1) if goal_ml > 0 else 0.0

            real_today = datetime.now(user_tz).date()
            streak = _compute_streak(daily_totals, real_today, goal_ml)

            result = {
                "date": target_date.isoformat(),
                "consumed_ml": consumed_ml,
                "goal_ml": goal_ml,
                "percentage": percentage,
                "streak": streak,
                "entries": entries_payload,
            }

        if self.cache_service:
            await self.cache_service.set_json(cache_key, result, ttl)
        return result
