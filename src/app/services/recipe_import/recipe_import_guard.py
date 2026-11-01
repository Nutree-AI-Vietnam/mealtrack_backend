"""Pre-flight checks for recipe imports: the server switch and a daily quota.

Each import may call the AI several times and download a video, so a
per-user daily quota sits on top of the per-minute route limit.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date

from src.api.exceptions import ExternalServiceException, TooManyRequestsException
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)

# (key, ttl_seconds) -> count after the increment, or None when Redis is down.
CounterIncrement = Callable[[str, int], Awaitable[int | None]]
RECIPE_IMPORT_QUOTA_TTL_SECONDS = 2 * 24 * 60 * 60


def recipe_import_quota_key(user_id: str, day: date) -> str:
    return f"recipe_import:quota:{user_id}:{day.isoformat()}"


@dataclass(frozen=True)
class RecipeImportGuard:
    enabled: bool
    daily_limit: int
    increment_counter: CounterIncrement

    def require_enabled(self) -> None:
        if not self.enabled:
            raise ExternalServiceException(
                "Recipe import is switched off", "RECIPE_IMPORT_DISABLED"
            )

    async def admit(self, user_id: str) -> None:
        """Raise when the switch is off or today's imports are used up."""
        self.require_enabled()
        count = await self.increment_counter(
            recipe_import_quota_key(user_id, utc_now().date()),
            RECIPE_IMPORT_QUOTA_TTL_SECONDS,
        )
        if count is None:
            # The per-minute limit still holds; a Redis outage must not block
            # imports entirely.
            logger.warning("Recipe import daily quota unavailable; allowing")
            return
        if count > self.daily_limit:
            raise TooManyRequestsException(
                "Daily recipe import limit reached",
                "RECIPE_IMPORT_DAILY_LIMIT",
                details={"daily_limit": self.daily_limit},
            )
