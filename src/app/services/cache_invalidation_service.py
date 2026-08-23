"""Centralized cache invalidation for all mutations.

Meal/hydration writes still await invalidation before the HTTP response so a
follow-up GET cannot hit a stale Redis key.

Movement writes persist first, then schedule invalidation on the process
task manager. The Flutter client already projects SQLite locally and must
not wait on Redis SCAN of `nutrition_bulk:*` / weekly-budget patterns.
"""

import logging
import time
from collections.abc import Coroutine
from datetime import date, timedelta
from typing import Any

from src.domain.cache.cache_keys import CacheKeys
from src.domain.ports.cache_port import CachePort

logger = logging.getLogger(__name__)


def _get_week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


class CacheInvalidationService:
    """Cache invalidation called by command handlers after a successful write."""

    def __init__(
        self,
        cache: CachePort | None,
        task_manager: Any | None = None,
    ):
        self._cache = cache
        self._task_manager = task_manager

    async def _invalidate_key(self, key: str) -> None:
        if not self._cache:
            return
        for attempt in range(2):
            try:
                await self._cache.invalidate(key)
                return
            except Exception as exc:
                if attempt == 0:
                    logger.warning("Cache invalidation retry for key=%s: %s", key, exc)
                else:
                    logger.error("Cache invalidation failed for key=%s: %s", key, exc)

    async def _invalidate_pattern(self, pattern: str) -> None:
        if not self._cache:
            return
        for attempt in range(2):
            try:
                await self._cache.invalidate_pattern(pattern)
                return
            except Exception as exc:
                if attempt == 0:
                    logger.warning("Cache pattern invalidation retry for %s: %s", pattern, exc)
                else:
                    logger.error("Cache pattern invalidation failed for %s: %s", pattern, exc)

    async def _invalidate_weekly_budget(self, user_id: str, week_start: date) -> None:
        await self._invalidate_key(CacheKeys.weekly_budget(user_id, week_start)[0])
        await self._invalidate_pattern(CacheKeys.weekly_budget_pattern(user_id, week_start))

    async def after_meal_write(self, user_id: str, meal_date: date) -> None:
        """Invalidate all caches affected by a meal create/edit/delete."""
        _t0 = time.perf_counter()
        meal_week_start = _get_week_start(meal_date)
        current_week_start = _get_week_start(date.today())

        # Critical keys (client reads immediately after write)
        _tc = time.perf_counter()
        await self._invalidate_pattern(
            f"user:{user_id}:activities:{meal_date.isoformat()}:*"
        )
        await self._invalidate_pattern(f"user:{user_id}:nutrition_bulk:*")
        await self._invalidate_key(CacheKeys.daily_macros(user_id, meal_date)[0])
        await self._invalidate_weekly_budget(user_id, meal_week_start)
        _critical_ms = (time.perf_counter() - _tc) * 1000

        # Secondary keys (read on other screens, slight delay acceptable)
        _ts = time.perf_counter()
        await self._invalidate_key(CacheKeys.daily_breakdown(user_id, meal_week_start)[0])
        await self._invalidate_key(CacheKeys.user_streak(user_id)[0])
        _secondary_ms = (time.perf_counter() - _ts) * 1000

        # Backdated meal: also invalidate current week
        if meal_week_start != current_week_start:
            await self._invalidate_weekly_budget(user_id, current_week_start)
            await self._invalidate_key(CacheKeys.daily_breakdown(user_id, current_week_start)[0])

        _total_ms = (time.perf_counter() - _t0) * 1000
        logger.info(
            "cache_invalidation timing: user=%s critical_ms=%.1f secondary_ms=%.1f total_ms=%.1f",
            user_id,
            _critical_ms,
            _secondary_ms,
            _total_ms,
        )

    async def after_movement_write(self, user_id: str, log_date: date) -> None:
        """Invalidate all caches affected by a movement log/edit/delete."""
        _t0 = time.perf_counter()
        week_start = _get_week_start(log_date)
        current_week_start = _get_week_start(date.today())

        # user_streak is intentionally NOT invalidated: the streak is meal-count
        # based, and movement entries don't create meals (unlike meals/caloric
        # drinks), so they cannot change it.
        await self._invalidate_pattern(
            f"user:{user_id}:activities:{log_date.isoformat()}:*"
        )
        await self._invalidate_pattern(f"user:{user_id}:nutrition_bulk:*")
        await self._invalidate_key(CacheKeys.daily_macros(user_id, log_date)[0])
        await self._invalidate_weekly_budget(user_id, week_start)
        await self._invalidate_key(CacheKeys.daily_breakdown(user_id, week_start)[0])

        # Backdated movement: also invalidate current week
        if week_start != current_week_start:
            await self._invalidate_weekly_budget(user_id, current_week_start)
            await self._invalidate_key(CacheKeys.daily_breakdown(user_id, current_week_start)[0])

        logger.info(
            "cache_invalidation movement timing: user=%s total_ms=%.1f",
            user_id,
            (time.perf_counter() - _t0) * 1000,
        )

    async def schedule_after_movement_write(self, user_id: str, log_date: date) -> None:
        """Run movement cache invalidation after the HTTP response when possible.

        Without a task manager (unit tests, scripts) this awaits in-process so
        callers still observe the same cache effects.
        """
        await self._schedule(
            f"cache:after_movement_write:{user_id}:{log_date.isoformat()}",
            self.after_movement_write(user_id, log_date),
        )

    async def _schedule(self, name: str, coro: Coroutine[Any, Any, Any]) -> None:
        if self._task_manager is not None:
            self._task_manager.spawn(name, coro)
            return
        await coro

    async def after_hydration_write(self, user_id: str, log_date: date) -> None:
        """Invalidate all caches affected by a hydration log/delete.

        Hydration affects meal-related caches too: caloric drinks contribute to
        daily macros and weekly budget. Previously two separate events were
        published (MealCacheInvalidationRequiredEvent + HydrationCacheInvalidationRequiredEvent).
        This method consolidates both key sets into one synchronous call.
        """
        week_start = _get_week_start(log_date)
        current_week_start = _get_week_start(date.today())

        # Hydration-specific keys
        await self._invalidate_pattern(
            f"user:{user_id}:activities:{log_date.isoformat()}:*"
        )
        await self._invalidate_pattern(f"user:{user_id}:nutrition_bulk:*")
        await self._invalidate_pattern(
            f"user:{user_id}:hydration:{log_date.isoformat()}:*"
        )
        await self._invalidate_key(CacheKeys.daily_macros(user_id, log_date)[0])
        await self._invalidate_key(CacheKeys.weekly_hydration(user_id, week_start)[0])

        # Meal-related keys (caloric drinks affect energy balance)
        await self._invalidate_weekly_budget(user_id, week_start)
        await self._invalidate_key(CacheKeys.daily_breakdown(user_id, week_start)[0])
        await self._invalidate_key(CacheKeys.user_streak(user_id)[0])

        # Backdated hydration: also invalidate current week
        if week_start != current_week_start:
            await self._invalidate_weekly_budget(user_id, current_week_start)
            await self._invalidate_key(CacheKeys.daily_breakdown(user_id, current_week_start)[0])

    async def after_custom_macros_update(self, user_id: str) -> None:
        """Invalidate caches affected by custom macro target changes.

        Custom macro targets are embedded in the cached daily_macros response,
        so ALL daily_macros entries for the user must be purged via pattern.
        """
        await self._invalidate_key(CacheKeys.user_tdee(user_id)[0])
        await self._invalidate_key(CacheKeys.user_profile(user_id)[0])
        # Purge ALL daily_macros for this user (targets are embedded in cached response)
        await self._invalidate_pattern(f"user:{user_id}:macros:*")
        await self._invalidate_pattern(f"user:{user_id}:nutrition_bulk:*")
        # Weekly budget for current and next week (covers timezone skew)
        today = date.today()
        this_week = _get_week_start(today)
        next_week = this_week + timedelta(days=7)
        await self._invalidate_weekly_budget(user_id, this_week)
        await self._invalidate_weekly_budget(user_id, next_week)
