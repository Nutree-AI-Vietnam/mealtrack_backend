"""Pre-flight checks shared by the Ghi nhanh describe routes.

The per-minute rate limit runs first, in the route decorator. This guard then
checks, in order: the server switch, Premium, the daily quota, and the sentence
itself. Nothing reaches the database, RevenueCat or the model while the switch
is off.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date

from src.api.exceptions import ExternalServiceException, ValidationException
from src.app.services.daily_quota import (
    DAILY_QUOTA_TTL_SECONDS,
    CounterIncrement,
    DailyQuota,
    daily_quota_key,
)
from src.domain.services.prompts.input_sanitizer import sanitize_user_description

# One counter per user per local day, shared by the water and activity routes.
DESCRIBE_QUOTA_SCOPE = "describe"
DESCRIBE_QUOTA_TTL_SECONDS = DAILY_QUOTA_TTL_SECONDS

SubscriptionCheck = Callable[[str], Awaitable[None]]


def describe_quota_key(user_id: str, local_day: date) -> str:
    return daily_quota_key(DESCRIBE_QUOTA_SCOPE, user_id, local_day)


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
        await DailyQuota(
            DESCRIBE_QUOTA_SCOPE,
            self.daily_limit,
            self.increment_counter,
            "DESCRIBE_DAILY_LIMIT",
            "Daily Ghi nhanh limit reached",
        ).consume(user_id, timezone)
        sanitized = sanitize_user_description(text)
        if not sanitized:
            raise ValidationException(
                "This sentence can't be logged", "DESCRIBE_TEXT_REJECTED"
            )
        return sanitized
