"""Cache service for recent and favorite meal list projections with revision invalidation."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from src.infra.cache.redis_client import RedisClient

logger = logging.getLogger(__name__)

LIST_CACHE_TTL = 300  # 5 minutes


class MealListCacheService:
    """Manages revision-based caching for user recent and favorite meals."""

    def __init__(self, redis_client: RedisClient, enabled: bool = True):
        self.redis = redis_client
        self.enabled = enabled

    @staticmethod
    def _revision_key(user_id: str) -> str:
        return f"user:{user_id}:meal-lists-revision:v1"

    @staticmethod
    def _recent_key(
        user_id: str, revision: int, timezone_str: str, language: str
    ) -> str:
        tz_hash = hashlib.sha256(timezone_str.encode("utf-8")).hexdigest()[:12]
        return f"user:{user_id}:recent-meals:v1:{revision}:{tz_hash}:{language}"

    @staticmethod
    def _favorite_key(user_id: str, revision: int, language: str) -> str:
        return f"user:{user_id}:favorite-meals:v1:{revision}:{language}"

    async def get_revision(self, user_id: str) -> int:
        """Get the current list revision for a user, defaulting to 0."""
        if not self.enabled:
            return 0
        try:
            val = await self.redis.get(self._revision_key(user_id))
            if val is not None:
                return int(val)
        except Exception as exc:
            logger.warning(
                "Failed to get meal list revision for user %s: %s", user_id, exc
            )
        return 0

    async def increment_revision(self, user_id: str) -> int:
        """Increment the user's meal list revision to invalidate cached lists."""
        if not self.enabled:
            return 0
        try:
            key = self._revision_key(user_id)
            val = await self.redis.incr(key)
            return val if val is not None else 0
        except Exception as exc:
            logger.warning(
                "Failed to increment meal list revision for user %s: %s",
                user_id,
                exc,
            )
            return 0

    async def get_recent_meals(
        self, user_id: str, revision: int, timezone_str: str, language: str
    ) -> dict[str, Any] | None:
        """Get cached recent meals payload."""
        if not self.enabled:
            return None
        try:
            key = self._recent_key(user_id, revision, timezone_str, language)
            raw = await self.redis.get(key)
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.warning(
                "Failed to get cached recent meals for user %s: %s", user_id, exc
            )
        return None

    async def set_recent_meals(
        self,
        user_id: str,
        revision: int,
        timezone_str: str,
        language: str,
        data: dict[str, Any],
        ttl: int = LIST_CACHE_TTL,
    ) -> None:
        """Cache recent meals payload."""
        if not self.enabled:
            return
        try:
            key = self._recent_key(user_id, revision, timezone_str, language)
            payload = json.dumps(data)
            await self.redis.set(key, payload, ttl=ttl)
        except Exception as exc:
            logger.warning(
                "Failed to set cached recent meals for user %s: %s", user_id, exc
            )

    async def get_favorite_meals(
        self, user_id: str, revision: int, language: str
    ) -> dict[str, Any] | None:
        """Get cached favorite meals payload."""
        if not self.enabled:
            return None
        try:
            key = self._favorite_key(user_id, revision, language)
            raw = await self.redis.get(key)
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.warning(
                "Failed to get cached favorite meals for user %s: %s",
                user_id,
                exc,
            )
        return None

    async def set_favorite_meals(
        self,
        user_id: str,
        revision: int,
        language: str,
        data: dict[str, Any],
        ttl: int = LIST_CACHE_TTL,
    ) -> None:
        """Cache favorite meals payload."""
        if not self.enabled:
            return
        try:
            key = self._favorite_key(user_id, revision, language)
            payload = json.dumps(data)
            await self.redis.set(key, payload, ttl=ttl)
        except Exception as exc:
            logger.warning(
                "Failed to set cached favorite meals for user %s: %s",
                user_id,
                exc,
            )
