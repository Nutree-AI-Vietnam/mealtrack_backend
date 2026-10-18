"""
GetStreakQueryHandler — computes current and best logging streak.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from src.app.events.base import EventHandler, handles
from src.app.queries.meal.get_streak_query import GetStreakQuery
from src.domain.cache.cache_keys import CacheKeys
from src.domain.services.vacation_rules import (
    best_streak,
    current_streak,
    to_window,
)
from src.domain.utils.timezone_utils import get_zone_info, resolve_user_timezone_async
from src.domain.ports.cache_port import CachePort
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(GetStreakQuery)
class GetStreakQueryHandler(EventHandler[GetStreakQuery, Dict[str, Any]]):
    """Handler that calculates current + best logging streak for a user."""

    def __init__(self, cache_service: Optional[CachePort] = None):
        self.cache_service = cache_service

    async def handle(self, query: GetStreakQuery) -> Dict[str, Any]:
        """Return current_streak, best_streak, and last_logged_date."""
        cache_key, ttl = CacheKeys.user_streak(query.user_id)
        if self.cache_service:
            cached = await self.cache_service.get_json(cache_key)
            if cached is not None:
                return cached
        result = await self._compute(query)
        if self.cache_service:
            await self.cache_service.set_json(cache_key, result, ttl)
        return result

    async def _compute(self, query: GetStreakQuery) -> Dict[str, Any]:
        """Compute streak from DB."""
        async with AsyncUnitOfWork() as uow:
            user_tz_str = await resolve_user_timezone_async(
                query.user_id, uow, query.header_timezone
            )
            user_tz = get_zone_info(user_tz_str)
            today = datetime.now(user_tz).date()

            dates = await uow.meals.get_dates_with_meals(
                query.user_id, user_timezone=user_tz_str
            )
            vacations = await uow.vacations.find_by_user(query.user_id)
            scan_count = await uow.meals.count_by_source(query.user_id, "scanner")

        if not dates:
            return {
                "current_streak": 0,
                "best_streak": 0,
                "last_logged_date": None,
                "scan_count": scan_count,
            }

        last_logged = dates[0]
        windows = [to_window(vacation) for vacation in vacations]
        logged = set(dates)

        return {
            "current_streak": current_streak(logged, today, windows),
            "best_streak": best_streak(logged, windows),
            "last_logged_date": last_logged.isoformat(),
            "scan_count": scan_count,
        }
