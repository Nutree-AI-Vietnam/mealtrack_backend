"""Build transactional cache invalidation events and retain legacy local jobs."""

import json
import logging
import time
from collections.abc import Coroutine
from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

from src.domain.cache.cache_invalidation_operations import (
    DELETE_KEY,
    DELETE_PATTERN,
    build_cheat_day_invalidation_operations,
    build_hydration_invalidation_operations,
    build_meal_invalidation_operations,
    build_movement_invalidation_operations,
    build_profile_invalidation_operations,
    build_saved_suggestion_invalidation_operations,
)
from src.domain.cache.cache_keys import CacheKeys
from src.domain.ports.cache_port import CachePort
from src.domain.ports.outbox_repository_port import OutboxRepositoryPort
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)

CACHE_INVALIDATION_EVENT_TYPE = "cache_invalidation.v1"
MAX_CACHE_EVENT_BYTES = 32 * 1024


def _get_week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


class CacheInvalidationService:
    """Enqueue cache maintenance without making Redis part of business flow."""

    def __init__(
        self,
        cache: CachePort | None,
        task_manager: Any | None = None,
        queue_enabled: bool = True,
    ):
        self._cache = cache
        self._task_manager = task_manager
        self._queue_enabled = queue_enabled

    async def _enqueue_invalidation_operations(
        self,
        outbox: OutboxRepositoryPort,
        user_id: str,
        operations: list[dict[str, str]],
        *,
        event_id: str | None = None,
    ) -> str | None:
        if self._cache is None or not self._queue_enabled:
            return None

        try:
            UUID(user_id)
        except ValueError as exc:
            raise ValueError("Cache invalidation user_id must be UUID") from exc

        resolved_event_id = event_id or str(uuid4())
        try:
            resolved_uuid = UUID(resolved_event_id)
        except ValueError as exc:
            raise ValueError("Cache invalidation event_id must be a UUID") from exc
        if resolved_uuid.version != 4:
            raise ValueError("Cache invalidation event_id must be UUID4")

        payload = {
            "version": 1,
            "event_type": CACHE_INVALIDATION_EVENT_TYPE,
            "event_id": resolved_event_id,
            "user_id": user_id,
            "occurred_at": utc_now().isoformat(),
            "operations": operations,
        }
        payload_size = len(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        )
        if payload_size > MAX_CACHE_EVENT_BYTES:
            raise ValueError("Cache invalidation event exceeds 32 KB")

        import inspect

        enqueue_res = outbox.enqueue(
            CACHE_INVALIDATION_EVENT_TYPE,
            payload,
            event_id=resolved_event_id,
            aggregate_type="user",
            aggregate_id=user_id,
        )
        if inspect.isawaitable(enqueue_res):
            await enqueue_res
        return resolved_event_id

    async def enqueue_meal_invalidation(
        self,
        outbox: OutboxRepositoryPort,
        user_id: str,
        meal_date: date,
        *,
        event_id: str | None = None,
        current_date: date | None = None,
    ) -> str | None:
        """Persist a meal cache event in the caller's active transaction."""
        return await self._enqueue_invalidation_operations(
            outbox,
            user_id,
            build_meal_invalidation_operations(
                user_id, meal_date, current_date=current_date
            ),
            event_id=event_id,
        )

    async def enqueue_hydration_invalidation(
        self,
        outbox: OutboxRepositoryPort,
        user_id: str,
        log_date: date,
        *,
        event_id: str | None = None,
        current_date: date | None = None,
    ) -> str | None:
        """Persist a hydration cache event in the caller's active transaction."""
        return await self._enqueue_invalidation_operations(
            outbox,
            user_id,
            build_hydration_invalidation_operations(
                user_id, log_date, current_date=current_date
            ),
            event_id=event_id,
        )

    async def enqueue_movement_invalidation(
        self,
        outbox: OutboxRepositoryPort,
        user_id: str,
        log_date: date,
        *,
        event_id: str | None = None,
        current_date: date | None = None,
    ) -> str | None:
        """Persist a movement cache event in the caller's active transaction."""
        return await self._enqueue_invalidation_operations(
            outbox,
            user_id,
            build_movement_invalidation_operations(
                user_id, log_date, current_date=current_date
            ),
            event_id=event_id,
        )

    async def enqueue_profile_invalidation(
        self,
        outbox: OutboxRepositoryPort,
        user_id: str,
        *,
        event_id: str | None = None,
    ) -> str | None:
        """Persist a profile/targets cache event in the caller's active transaction."""
        return await self._enqueue_invalidation_operations(
            outbox,
            user_id,
            build_profile_invalidation_operations(user_id),
            event_id=event_id,
        )

    async def enqueue_cheat_day_invalidation(
        self,
        outbox: OutboxRepositoryPort,
        user_id: str,
        cheat_day: date,
        *,
        event_id: str | None = None,
    ) -> str | None:
        """Persist a cheat day cache event in the caller's active transaction."""
        return await self._enqueue_invalidation_operations(
            outbox,
            user_id,
            build_cheat_day_invalidation_operations(user_id, cheat_day),
            event_id=event_id,
        )

    async def enqueue_saved_suggestion_invalidation(
        self,
        outbox: OutboxRepositoryPort,
        user_id: str,
        *,
        event_id: str | None = None,
    ) -> str | None:
        """Persist a saved suggestion cache event in the caller's active transaction."""
        return await self._enqueue_invalidation_operations(
            outbox,
            user_id,
            build_saved_suggestion_invalidation_operations(user_id),
            event_id=event_id,
        )

    async def _invalidate_key(self, key: str) -> None:
        if not self._cache:
            return
        invalidate = getattr(self._cache, "invalidate_now", None)
        if invalidate is None:
            invalidate = self._cache.invalidate
        for attempt in range(2):
            try:
                await invalidate(key)
                return
            except Exception as exc:
                if attempt == 0:
                    logger.warning("Cache invalidation retry for key=%s: %s", key, exc)
                else:
                    logger.error("Cache invalidation failed for key=%s: %s", key, exc)

    async def _invalidate_pattern(self, pattern: str) -> None:
        if not self._cache:
            return
        invalidate_pattern = getattr(self._cache, "invalidate_pattern_now", None)
        if invalidate_pattern is None:
            invalidate_pattern = self._cache.invalidate_pattern
        for attempt in range(2):
            try:
                await invalidate_pattern(pattern)
                return
            except Exception as exc:
                if attempt == 0:
                    logger.warning(
                        "Cache pattern invalidation retry for %s: %s", pattern, exc
                    )
                else:
                    logger.error(
                        "Cache pattern invalidation failed for %s: %s", pattern, exc
                    )

    async def _invalidate_weekly_budget(self, user_id: str, week_start: date) -> None:
        await self._invalidate_key(CacheKeys.weekly_budget(user_id, week_start)[0])
        await self._invalidate_pattern(
            CacheKeys.weekly_budget_pattern(user_id, week_start)
        )

    async def _schedule(
        self,
        name: str,
        coro: Coroutine[Any, Any, Any],
    ) -> None:
        if self._cache is None:
            coro.close()
            return
        if self._task_manager is None:
            logger.error(
                "Cache job dropped because no background task manager is "
                "configured: %s",
                name,
            )
            coro.close()
            return
        try:
            self._task_manager.spawn(name, coro)
        except Exception:
            coro.close()
            logger.error("Failed to enqueue cache job: %s", name, exc_info=True)

    async def after_meal_write(self, user_id: str, meal_date: date) -> None:
        """Enqueue every meal-derived cache projection after SQL commit."""
        week_start = _get_week_start(meal_date)
        current_week_start = _get_week_start(date.today())
        started = time.perf_counter()
        await self._schedule(
            f"cache:after_meal_write:{user_id}:{meal_date.isoformat()}",
            self._run_meal_invalidations(
                user_id, meal_date, week_start, current_week_start
            ),
        )
        logger.info(
            "cache_invalidation timing: user=%s enqueue_ms=%.1f "
            "total_ms=%.1f queued=true",
            user_id,
            (time.perf_counter() - started) * 1000,
            (time.perf_counter() - started) * 1000,
        )

    async def _run_meal_invalidations(
        self,
        user_id: str,
        meal_date: date,
        meal_week_start: date,
        current_week_start: date,
    ) -> None:
        await self._run_operations(
            build_meal_invalidation_operations(user_id, meal_date)
        )

    async def _run_operations(self, operations: list[dict[str, str]]) -> None:
        for operation in operations:
            if operation["op"] == DELETE_KEY:
                await self._invalidate_key(operation["key"])
            elif operation["op"] == DELETE_PATTERN:
                await self._invalidate_pattern(operation["pattern"])
            else:
                logger.error(
                    "Unsupported cache invalidation operation: %s",
                    operation.get("op"),
                )

    async def after_movement_write(self, user_id: str, log_date: date) -> None:
        """Enqueue every movement-derived cache projection after SQL commit."""
        week_start = _get_week_start(log_date)
        current_week_start = _get_week_start(date.today())
        await self._schedule(
            f"cache:after_movement_write:{user_id}:{log_date.isoformat()}",
            self._run_movement_invalidations(
                user_id, log_date, week_start, current_week_start
            ),
        )

    async def _run_movement_invalidations(
        self,
        user_id: str,
        log_date: date,
        week_start: date,
        current_week_start: date,
    ) -> None:
        await self._run_operations(
            build_movement_invalidation_operations(user_id, log_date)
        )

    async def schedule_after_movement_write(self, user_id: str, log_date: date) -> None:
        """Compatibility alias for callers using the former method name."""
        await self.after_movement_write(user_id, log_date)

    async def after_hydration_write(self, user_id: str, log_date: date) -> None:
        """Enqueue hydration and caloric-drink projections after SQL commit."""
        week_start = _get_week_start(log_date)
        current_week_start = _get_week_start(date.today())
        await self._schedule(
            f"cache:after_hydration_write:{user_id}:{log_date.isoformat()}",
            self._run_hydration_invalidations(
                user_id, log_date, week_start, current_week_start
            ),
        )

    async def _run_hydration_invalidations(
        self,
        user_id: str,
        log_date: date,
        week_start: date,
        current_week_start: date,
    ) -> None:
        await self._run_operations(
            build_hydration_invalidation_operations(user_id, log_date)
        )

    async def after_custom_macros_update(self, user_id: str) -> None:
        """Compatibility alias for profile-derived target updates."""
        await self.after_profile_write(user_id)

    async def after_profile_write(self, user_id: str) -> None:
        """Enqueue every projection affected by a profile or target update."""
        await self._schedule(
            f"cache:after_profile_write:{user_id}",
            self._run_profile_invalidations(user_id),
        )

    async def _run_profile_invalidations(self, user_id: str) -> None:
        await self._run_operations(build_profile_invalidation_operations(user_id))

    async def after_cheat_day_write(self, user_id: str, cheat_day: date) -> None:
        """Enqueue weekly-budget maintenance after a cheat-day mutation."""
        await self._schedule(
            f"cache:after_cheat_day_write:{user_id}:{cheat_day.isoformat()}",
            self._run_cheat_day_invalidations(user_id, cheat_day),
        )

    async def _run_cheat_day_invalidations(self, user_id: str, cheat_day: date) -> None:
        await self._run_operations(
            build_cheat_day_invalidation_operations(user_id, cheat_day)
        )

    async def after_saved_suggestion_write(self, user_id: str) -> None:
        """Enqueue saved-suggestion cache maintenance after SQL commit."""
        await self._schedule(
            f"cache:after_saved_suggestion_write:{user_id}",
            self._run_operations(
                build_saved_suggestion_invalidation_operations(user_id)
            ),
        )
