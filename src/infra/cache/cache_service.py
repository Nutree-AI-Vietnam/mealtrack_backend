"""
High-level cache service that handles serialization and metrics.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel

from src.domain.ports.cache_port import CachePort
from src.infra.cache.metrics import CacheMonitor
from src.infra.cache.redis_client import RedisClient

# Strip the trailing 'Z' produced by an older serializer bug that wrote
# tz-aware datetimes as '...+HH:MMZ' (offset + Z together is invalid ISO8601
# and rejected by Pydantic v2). Heals existing Redis entries on read.
_LEGACY_OFFSET_Z_RE = re.compile(r"([+-]\d{2}:\d{2})Z")

T = TypeVar("T")
logger = logging.getLogger(__name__)


class CacheService(CachePort):
    """Cache service implementing the cache-aside pattern."""

    def __init__(
        self,
        redis_client: RedisClient,
        default_ttl: int = 3600,
        monitor: CacheMonitor | None = None,
        enabled: bool = True,
    ):
        self.redis = redis_client
        self.default_ttl = default_ttl
        self.monitor = monitor
        self.enabled = enabled

    async def get(self, key: str) -> Any | None:
        """Implement CachePort.get — delegates to get_json."""
        return await self.get_json(key)

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Implement CachePort.set — delegates to set_json."""
        await self.set_json(key, value, ttl_seconds)

    async def get_json(self, key: str) -> Any | None:
        """Retrieve and deserialize a cached JSON payload."""
        if not self.enabled:
            return None

        raw = await self.redis.get(key)
        if raw is None:
            if self.monitor:
                self.monitor.record_miss()
            return None

        if self.monitor:
            self.monitor.record_hit()

        try:
            sanitized = (
                _LEGACY_OFFSET_Z_RE.sub(r"\1", raw) if isinstance(raw, str) else raw
            )
            return json.loads(sanitized)
        except json.JSONDecodeError:
            return None

    async def set_json(
        self,
        key: str,
        value: Any,
        ttl: int | None = None,
        *,
        revision_field: str | None = None,
    ) -> bool:
        """Write a cached value asynchronously."""
        return await self.set_json_now(
            key, value, ttl, revision_field=revision_field
        )

    async def set_json_now(
        self,
        key: str,
        value: Any,
        ttl: int | None = None,
        *,
        revision_field: str | None = None,
    ) -> bool:
        """Write directly to Redis from a background cache job."""
        if not self.enabled:
            return False

        payload: str
        if isinstance(value, BaseModel):
            payload = value.model_dump_json()
        else:
            payload = json.dumps(value, default=_json_serializer)

        effective_ttl = ttl or self.default_ttl
        if revision_field and isinstance(value, dict):
            revision = value.get(revision_field)
            if isinstance(revision, int) and not isinstance(revision, bool):
                return await self.redis.set_if_revision_newer(
                    key, payload, revision, effective_ttl
                )
        return await self.redis.set(key, payload, effective_ttl)

    @staticmethod
    def _key_hash(key: str) -> str:
        """Keep Redis keys out of task names and operational logs."""
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[T]],
        ttl: int | None = None,
    ) -> T | None:
        """
        Cache-aside helper that fetches data from cache or executes the factory.
        """
        cached = await self.get_json(key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        value = await factory()
        if value is not None:
            await self.set_json(key, value, ttl)
        return value

    async def invalidate(self, key: str) -> bool:
        """Remove a cached value."""
        return await self.invalidate_now(key)

    async def invalidate_now(self, key: str) -> bool:
        """Remove a cached value directly."""
        if not self.enabled:
            return False
        deleted, _ = await asyncio.gather(
            self.redis.delete(key),
            self.redis.delete(f"{key}:__revision"),
        )
        return bool(deleted)

    async def invalidate_pattern(self, pattern: str) -> int:
        """Remove all cache keys matching a glob pattern."""
        return await self.invalidate_pattern_now(pattern)

    async def invalidate_pattern_now(self, pattern: str) -> int:
        """Remove matching keys from inside a background cache job."""
        if not self.enabled:
            return 0
        return await self.redis.delete_pattern(pattern)


def _json_serializer(value: Any) -> Any:
    """Helper to serialize objects that aren't JSON-serializable by default."""
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, datetime):
        # tz-aware datetimes already encode the offset (e.g. +00:00); only
        # append 'Z' for naive datetimes (assumed UTC) to avoid producing
        # malformed strings like '...+00:00Z' that Pydantic rejects.
        return (
            value.isoformat() if value.tzinfo is not None else value.isoformat() + "Z"
        )
    raise TypeError(
        f"Object of type {value.__class__.__name__} is not JSON serializable"
    )
