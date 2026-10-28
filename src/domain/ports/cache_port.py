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

    async def add(self, key: str, value: Any, ttl_seconds: int) -> bool | None:
        """Store a value under a TTL only when the key holds nothing.

        True when this call stored it, False when the key already held a
        value, None when the cache cannot tell. Adapters do it atomically and
        inline; this fallback reads, then writes, so two callers may both win.
        """
        if await self.get(key) is not None:
            return False
        await self.set(key, value, ttl_seconds)
        return True

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

    async def purge(self, keys: list[str], revisions: dict[str, int]) -> None:
        """Delete entries and advance write counters (key -> TTL seconds).

        Adapters do both in one round-trip; this fallback cannot advance
        counters, which is safe because it also never reads one back.
        """
        for key in keys:
            await self.invalidate(key)

    async def get_json_with_revision(
        self, key: str, revision_key: str
    ) -> tuple[Any | None, int | None]:
        """Return a cached payload and the write counter that fences it.

        The counter reads 0 when unset; None means unknown, so a cache fenced
        by it must not be served.
        """
        return await self.get(key), None
