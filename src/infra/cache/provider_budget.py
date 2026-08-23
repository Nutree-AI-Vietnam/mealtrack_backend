"""Shared provider request budgets."""

import asyncio
import time

from src.domain.ports.provider_budget_port import ProviderBudgetPort
from src.infra.cache.redis_client import RedisClient


class MemoryProviderBudget:
    """Process-local rolling window. Used only when Redis is unavailable in development."""

    def __init__(self, window_seconds: int = 60):
        self.window_seconds = window_seconds
        self._counts: dict[tuple[str, int], int] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, namespace: str, limit: int) -> bool:
        if limit < 1:
            return False
        window = int(time.time() // self.window_seconds)
        key = (namespace, window)
        async with self._lock:
            count = self._counts.get(key, 0) + 1
            self._counts[key] = count
        return count <= limit


class RedisProviderBudget(ProviderBudgetPort):
    """Count provider calls in a short shared window and fail closed on Redis errors."""

    def __init__(self, redis_client: RedisClient, window_seconds: int = 60):
        self.redis = redis_client
        self.window_seconds = window_seconds

    async def acquire(self, namespace: str, limit: int) -> bool:
        if limit < 1:
            return False
        window = int(time.time() // self.window_seconds)
        key = f"nutrition:provider-budget:v1:{namespace}:{window}"
        count = await self.redis.incr_with_expiry(key, self.window_seconds + 1)
        return count is not None and count <= limit
