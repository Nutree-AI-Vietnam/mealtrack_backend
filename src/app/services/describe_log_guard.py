"""Pre-flight checks shared by the Ghi nhanh describe routes.

The per-minute rate limit runs first, in the route decorator. This guard then
checks, in order: the server switch, Premium, the daily quota, and the sentence
itself. Nothing reaches the database, RevenueCat or the model while the switch
is off.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date

from src.api.exceptions import (
    ExternalServiceException,
    TooManyRequestsException,
    ValidationException,
)
from src.domain.services.prompts.input_sanitizer import sanitize_user_description
from src.domain.utils.timezone_utils import get_zone_info, utc_now

logger = logging.getLogger(__name__)

# One counter per user per local day, shared by the water and activity routes.
DESCRIBE_QUOTA_SCOPE = "describe"
# Outlives any local day the key can name, whatever the person's UTC offset.
DESCRIBE_QUOTA_TTL_SECONDS = 2 * 24 * 60 * 60

SubscriptionCheck = Callable[[str], Awaitable[None]]
# (key, ttl_seconds) -> count after the increment, or None when Redis is down.
CounterIncrement = Callable[[str, int], Awaitable[int | None]]


def describe_quota_key(user_id: str, local_day: date) -> str:
    return f"{DESCRIBE_QUOTA_SCOPE}:quota:{user_id}:{local_day.isoformat()}"


@dataclass(frozen=True)
class DescribeLogGuard:
    enabled: bool
    daily_limit: int
    require_subscription: SubscriptionCheck
    increment_counter: CounterIncrement

    async def check(
        self, *, user_id: str, text: str, timezone: str | None = None
    ) -> str:
        """Run every pre-flight check and return the sanitised sentence."""
        if not self.enabled:
            raise ExternalServiceException(
                "Ghi nhanh is switched off", "FEATURE_DISABLED"
            )
        await self.require_subscription(user_id)
        await self._consume_daily_quota(user_id, timezone)
        sanitized = sanitize_user_description(text)
        if not sanitized:
            raise ValidationException(
                "This sentence can't be logged", "DESCRIBE_TEXT_REJECTED"
            )
        return sanitized

    async def _consume_daily_quota(self, user_id: str, timezone: str | None) -> None:
        local_day = utc_now().astimezone(get_zone_info(timezone or "UTC")).date()
        count = await self.increment_counter(
            describe_quota_key(user_id, local_day), DESCRIBE_QUOTA_TTL_SECONDS
        )
        if count is None:
            # The per-minute limit still holds; a Redis outage must not lock
            # paying users out.
            logger.warning("Describe daily quota unavailable; allowing the request")
            return
        if count > self.daily_limit:
            raise TooManyRequestsException(
                "Daily Ghi nhanh limit reached", "DESCRIBE_DAILY_LIMIT"
            )
