"""Abstract cache port — domain defines the contract, infra implements it."""

from abc import ABC, abstractmethod
from typing import Any


class CachePort(ABC):
    """Interface for cache operations used by application handlers."""

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """Return cached value or None if missing/expired."""

    @abstractmethod
    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Request storage of a value under a TTL.

        Implementations may enqueue this cache population instead of writing
        to Redis inline with the business request.
        """

    @abstractmethod
    async def invalidate(self, key: str) -> bool:
        """Request deletion of one cache entry.

        Implementations may enqueue this non-business maintenance operation;
        callers must not depend on the return value as a read-after-write
        consistency signal.
        """

    @abstractmethod
    async def invalidate_pattern(self, pattern: str) -> int:
        """Request deletion of cache entries matching a glob pattern."""

    async def invalidate_many(self, keys: list[str]) -> int:
        """Delete several cache entries; adapters override this with one call."""
        deleted = 0
        for key in keys:
            deleted += int(await self.invalidate(key))
        return deleted

    async def read_revision(self, key: str) -> int | None:
        """Return a write counter: 0 when unset, None when it cannot be read.

        None means unknown, so a cache fenced by the counter must not be served.
        """
        return None

    async def bump_revision(self, key: str, ttl_seconds: int) -> int | None:
        """Advance a write counter so entries built at the old value stop serving."""
        return None
