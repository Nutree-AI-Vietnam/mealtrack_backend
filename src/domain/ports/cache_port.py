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
