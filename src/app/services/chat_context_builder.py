"""Assemble a versioned, bounded snapshot of authoritative Nutree facts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from src.app.handlers.query_handlers.get_daily_macros_query_handler import (
    GetDailyMacrosQueryHandler,
)
from src.app.handlers.query_handlers.get_user_profile_query_handler import (
    GetUserProfileQueryHandler,
)
from src.app.handlers.query_handlers.get_user_tdee_query_handler import (
    GetUserTdeeQueryHandler,
)
from src.app.queries.meal import GetDailyMacrosQuery
from src.app.queries.tdee import GetUserTdeeQuery
from src.app.queries.user import GetUserProfileQuery
from src.domain.model.chat import (
    CHAT_CONTEXT_VERSION,
    CHAT_RECENT_MEAL_DAYS,
    CHAT_RECENT_MEAL_LIMIT,
    ChatMealSummary,
    ChatUserContext,
)
from src.domain.model.meal import MealStatus
from src.domain.model.meal_projection import MealProjection
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.cache_port import CachePort
from src.domain.services.chat.meal_slot import slot_from_local_datetime
from src.domain.services.meal_calorie_service import effective_meal_calories
from src.domain.utils.timezone_utils import (
    format_iso_utc,
    get_zone_info,
    resolve_user_timezone_async,
    utc_now,
)


class ChatContextBuilder:
    """Personal facts are not RAG; they come from Nutree's SQL projections."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], AsyncUnitOfWorkPort],
        cache_service: CachePort | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._cache_service = cache_service
        self._profile_handler = GetUserProfileQueryHandler(cache_service=cache_service)
        self._tdee_handler = GetUserTdeeQueryHandler(cache_service=cache_service)
        self._daily_handler = GetDailyMacrosQueryHandler(cache_service=cache_service)

    async def build(
        self,
        *,
        user_id: str,
        locale: str,
        header_timezone: str | None,
    ) -> ChatUserContext:
        missing: list[str] = []
        async with self._uow_factory() as uow:
            timezone = await resolve_user_timezone_async(user_id, uow, header_timezone)
            zone = get_zone_info(timezone)
            local_now = datetime.now(zone)
            today = local_now.date()
            local_hour, local_minute, suggested_slot = slot_from_local_datetime(
                local_now
            )
            recent_meals = await self._recent_meals(uow, user_id, today, timezone)

        profile = await self._safe_profile(user_id, missing)
        tdee = await self._safe_tdee(user_id, missing)
        daily = await self._safe_daily(user_id, today, header_timezone, missing)

        allergies = _list_or_none((profile or {}).get("profile", {}).get("allergies"))
        health = _list_or_none(
            (profile or {}).get("profile", {}).get("health_conditions")
        )
        diet = _list_or_none(
            (profile or {}).get("profile", {}).get("dietary_preferences")
        )
        goal = (profile or {}).get("profile", {}).get("fitness_goal")

        weekly = (daily or {}).get("weekly_context") or {}
        target_calories = weekly.get("adjusted_target_calories")
        target_protein = weekly.get("daily_protein")
        target_carbs = weekly.get("adjusted_target_carbs")
        target_fat = weekly.get("adjusted_target_fat")
        remaining_days = weekly.get("remaining_days")
        if target_calories is None and daily:
            target_calories = daily.get("target_calories")
            macros = daily.get("target_macros") or {}
            target_protein = macros.get("protein")
            target_carbs = macros.get("carbs")
            target_fat = macros.get("fat")

        consumed_calories = (daily or {}).get("total_calories")
        consumed_protein = (daily or {}).get("total_protein")
        consumed_carbs = (daily or {}).get("total_carbs")
        consumed_fat = (daily or {}).get("total_fat")

        return ChatUserContext(
            context_version=CHAT_CONTEXT_VERSION,
            as_of=format_iso_utc(utc_now()) or utc_now().isoformat(),
            locale=locale,
            timezone=timezone,
            allergies=allergies,
            health_conditions=health,
            dietary_preferences=diet,
            goal=goal,
            tdee=(tdee or {}).get("tdee"),
            target_calories=_num(target_calories),
            target_protein_g=_num(target_protein),
            target_carbs_g=_num(target_carbs),
            target_fat_g=_num(target_fat),
            consumed_calories=_num(consumed_calories),
            consumed_protein_g=_num(consumed_protein),
            consumed_carbs_g=_num(consumed_carbs),
            consumed_fat_g=_num(consumed_fat),
            remaining_calories=_remaining(target_calories, consumed_calories),
            remaining_protein_g=_remaining(target_protein, consumed_protein),
            remaining_carbs_g=_remaining(target_carbs, consumed_carbs),
            remaining_fat_g=_remaining(target_fat, consumed_fat),
            remaining_days=int(remaining_days) if remaining_days is not None else None,
            local_hour=local_hour,
            local_minute=local_minute,
            suggested_meal_slot=suggested_slot,
            recent_meals=tuple(recent_meals),
            missing=tuple(dict.fromkeys(missing)),
        )

    async def _recent_meals(
        self,
        uow: AsyncUnitOfWorkPort,
        user_id: str,
        today: date,
        timezone: str,
    ) -> list[ChatMealSummary]:
        start = today - timedelta(days=CHAT_RECENT_MEAL_DAYS - 1)
        meals = await uow.meals.find_by_date_range(
            user_id=user_id,
            start_date=start,
            end_date=today,
            limit=CHAT_RECENT_MEAL_LIMIT,
            user_timezone=timezone,
            projection=MealProjection.MACROS_ONLY,
        )
        zone = get_zone_info(timezone)
        summaries: list[ChatMealSummary] = []
        for meal in meals:
            if meal.status == MealStatus.INACTIVE:
                continue
            local_date = meal.created_at.astimezone(zone).date().isoformat()
            macros = getattr(getattr(meal, "nutrition", None), "macros", None)
            summaries.append(
                ChatMealSummary(
                    meal_id=meal.meal_id,
                    local_date=local_date,
                    dish_name=meal.dish_name,
                    meal_type=meal.meal_type,
                    calories=effective_meal_calories(meal) if meal.nutrition else None,
                    protein_g=_num(getattr(macros, "protein", None)),
                    carbs_g=_num(getattr(macros, "carbs", None)),
                    fat_g=_num(getattr(macros, "fat", None)),
                    status=str(meal.status) if meal.status else None,
                )
            )
            if len(summaries) >= CHAT_RECENT_MEAL_LIMIT:
                break
        return summaries[-CHAT_RECENT_MEAL_LIMIT:]

    async def _safe_profile(
        self, user_id: str, missing: list[str]
    ) -> dict[str, Any] | None:
        try:
            return await self._profile_handler.handle(
                GetUserProfileQuery(user_id=user_id)
            )
        except Exception:
            missing.append("profile")
            return None

    async def _safe_tdee(
        self, user_id: str, missing: list[str]
    ) -> dict[str, Any] | None:
        try:
            return await self._tdee_handler.handle(GetUserTdeeQuery(user_id=user_id))
        except Exception:
            missing.append("tdee")
            return None

    async def _safe_daily(
        self,
        user_id: str,
        today: date,
        header_timezone: str | None,
        missing: list[str],
    ) -> dict[str, Any] | None:
        try:
            return await self._daily_handler.handle(
                GetDailyMacrosQuery(
                    user_id=user_id,
                    target_date=today,
                    header_timezone=header_timezone,
                )
            )
        except Exception:
            missing.append("daily_progress")
            return None


def _list_or_none(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value]
    return None


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _remaining(target: Any, consumed: Any) -> float | None:
    target_n = _num(target)
    consumed_n = _num(consumed)
    if target_n is None or consumed_n is None:
        return None
    return round(target_n - consumed_n, 1)
