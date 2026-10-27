"""Ask the Worker again for a meal's value insights when its result never came.

The backend only reads `meal_insight:{meal_id}`; the nutreeai_async Worker
writes it after consuming the meal's Queue event. When that event is lost
(best-effort publish, Queue retention after a Worker outage) the key never
appears and the app shows no meal review. Re-publishing the meal's event
from the status read lets the Worker generate it while the app is polling.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from src.app.events.meal.meal_events import publish_meal_event
from src.domain.model.meal import Meal, MealStatus
from src.domain.ports.cache_port import CachePort
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)

# The Worker usually answers in 15-40 s; asking sooner would double its work.
INSIGHT_RECOVERY_DELAY = timedelta(minutes=2)
# Matches the Worker's seven-day insight TTL: older meals were never kept.
INSIGHT_RECOVERY_WINDOW = timedelta(days=7)
INSIGHT_RECOVERY_COOLDOWN_SECONDS = 10 * 60
INSIGHT_RECOVERY_GUARD_PREFIX = "meal_insight_recovery:"


class MealInsightRecovery:
    """Re-publishes a meal's Queue event, at most once per cooldown."""

    def __init__(
        self,
        publisher: Any,
        environment: str,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._publisher = publisher
        self._environment = environment
        self._clock = clock

    async def request_if_missing(
        self,
        *,
        meal: Meal,
        cache_service: CachePort,
        language: str,
        event_bus: Any | None = None,
    ) -> bool:
        """Re-publish [meal]'s event when its insight is overdue.

        Call only after the insight key read came back empty. Returns True
        when an event was published. Never raises: the status read must not
        fail because recovery could not run.
        """
        now = self._clock()
        if not _is_overdue(meal, now):
            return False
        guard_key = f"{INSIGHT_RECOVERY_GUARD_PREFIX}{meal.meal_id}"
        try:
            if await cache_service.get(guard_key) is not None:
                return False
            await cache_service.set(guard_key, 1, INSIGHT_RECOVERY_COOLDOWN_SECONDS)
            published = await publish_meal_event(
                self._publisher,
                meal,
                event_type="updated",
                environment=self._environment,
                meal_date=_as_utc(meal.created_at or now).date(),
                user_id=meal.user_id,
                language=language,
                event_bus=event_bus,
                source="meal_insight_recovery",
            )
        except Exception as exc:
            logger.warning(
                "meal_value_insights.recovery_failed meal_id=%s error=%s",
                meal.meal_id,
                type(exc).__name__,
            )
            return False
        logger.info("meal_value_insights.recovery_requested meal_id=%s", meal.meal_id)
        return bool(published)


def _is_overdue(meal: Meal, now: datetime) -> bool:
    if meal.status != MealStatus.READY or meal.nutrition is None:
        return False
    # Drinks read as meals here but publish hydration events, never insights.
    if meal.meal_type == "hydration":
        return False
    logged_at = meal.ready_at or meal.created_at
    if logged_at is None:
        return False
    age = now - _as_utc(logged_at)
    return INSIGHT_RECOVERY_DELAY <= age <= INSIGHT_RECOVERY_WINDOW


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
