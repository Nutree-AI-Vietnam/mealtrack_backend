"""A per-user, per-local-day counter that refuses requests past a limit."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date

from src.api.exceptions import TooManyRequestsException
from src.domain.utils.timezone_utils import get_zone_info, utc_now

logger = logging.getLogger(__name__)

# Outlives any local day the key can name, whatever the person's UTC offset.
DAILY_QUOTA_TTL_SECONDS = 2 * 24 * 60 * 60

# (key, ttl_seconds) -> count after the increment, or None when Redis is down.
CounterIncrement = Callable[[str, int], Awaitable[int | None]]


def daily_quota_key(scope: str, user_id: str, local_day: date) -> str:
    return f"{scope}:quota:{user_id}:{local_day.isoformat()}"


@dataclass(frozen=True)
class DailyQuota:
    scope: str
    daily_limit: int
    increment_counter: CounterIncrement
    error_code: str
    message: str

    async def consume(self, user_id: str, timezone: str | None) -> None:
        """Count one use for the user's local day; 429 once the limit is spent."""
        local_day = utc_now().astimezone(get_zone_info(timezone or "UTC")).date()
        count = await self.increment_counter(
            daily_quota_key(self.scope, user_id, local_day), DAILY_QUOTA_TTL_SECONDS
        )
        if count is None:
            # The per-minute limit still holds; a Redis outage must not lock
            # paying users out.
            logger.warning(
                "%s daily quota unavailable; allowing the request", self.scope
            )
            return
        if count > self.daily_limit:
            raise TooManyRequestsException(self.message, self.error_code)
