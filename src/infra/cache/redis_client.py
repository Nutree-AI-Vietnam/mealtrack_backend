"""
Async Redis client with connection pooling.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional, TypeVar
from urllib.parse import urlparse

import redis.asyncio as redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)
T = TypeVar("T")


def _safe_redis_log_label(url: str) -> str:
    """Return scheme + host + port for logs; never userinfo (password)."""
    parsed = urlparse(url)
    host = parsed.hostname or "unknown"
    port = f":{parsed.port}" if parsed.port else ""
    scheme = parsed.scheme or "redis"
    return f"{scheme}://{host}{port}"


class RedisClient:
    """Wrapper around redis.asyncio client that manages a shared pool."""

    def __init__(
        self,
        redis_url: str,
        max_connections: int = 50,
        socket_timeout: float = 5.0,
        socket_connect_timeout: float = 5.0,
    ):
        self._redis_url = redis_url
        self._max_connections = max_connections
        self._socket_timeout = socket_timeout
        self._socket_connect_timeout = socket_connect_timeout
        self._loop_id: Optional[int] = None
        self.pool = self._create_pool()
        self.client: Optional[redis.Redis] = None

    def _create_pool(self) -> redis.ConnectionPool:
        return redis.ConnectionPool.from_url(
            self._redis_url,
            max_connections=self._max_connections,
            decode_responses=True,
            socket_timeout=self._socket_timeout,
            socket_connect_timeout=self._socket_connect_timeout,
        )

    @staticmethod
    def _current_loop_id() -> int:
        return id(asyncio.get_running_loop())

    async def _reset_client(self) -> None:
        old_client = self.client
        old_pool = self.pool
        self.client = None
        self._loop_id = None

        if old_client:
            try:
                await old_client.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Redis client close failed during reset: %s", exc)
        try:
            await old_pool.disconnect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis pool disconnect failed during reset: %s", exc)

        self.pool = self._create_pool()

    async def connect(self) -> None:
        """Initialize the Redis connection."""
        current_loop_id = self._current_loop_id()
        if (
            self.client is not None
            and getattr(self, "_loop_id", current_loop_id) == current_loop_id
        ):
            return
        if self.client is not None:
            logger.warning("Redis client loop changed; reconnecting cache client")
            await self._reset_client()

        self.client = redis.Redis(connection_pool=self.pool)
        try:
            await self.client.ping()
            self._loop_id = current_loop_id
            logger.info("Redis connected (%s)", _safe_redis_log_label(self._redis_url))
        except RedisError as exc:  # pragma: no cover - connection errors logged
            logger.error(
                "Failed to connect to Redis (%s): %s",
                _safe_redis_log_label(self._redis_url),
                exc,
            )
            raise

    async def _with_client(
        self,
        operation_name: str,
        operation: Callable[[redis.Redis], Awaitable[T]],
        fallback: T,
        key: Optional[str] = None,
    ) -> T:
        log_key = f" for key {key}" if key else ""
        for attempt in range(2):
            try:
                await self.connect()
                if not self.client:
                    return fallback
                return await operation(self.client)
            except RuntimeError as exc:
                if "attached to a different loop" in str(exc) and attempt == 0:
                    logger.warning(
                        "Redis %s loop mismatch%s; reconnecting",
                        operation_name,
                        log_key,
                    )
                    await self._reset_client()
                    continue
                logger.warning(
                    "Redis %s runtime error%s: %s", operation_name, log_key, exc
                )
                return fallback
            except RedisError as exc:
                logger.warning("Redis %s error%s: %s", operation_name, log_key, exc)
                return fallback
        return fallback

    async def disconnect(self) -> None:
        """Close Redis connections."""
        if self.client:
            await self.client.aclose()
            self.client = None

        await self.pool.disconnect()
        self._loop_id = None
        logger.info("Redis disconnected")

    async def get(self, key: str) -> Optional[str]:
        """Retrieve a cached value."""
        return await self._with_client("GET", lambda client: client.get(key), None, key)

    async def set(self, key: str, value: str, ttl: Optional[int] = None) -> bool:
        """Store a value with optional TTL."""

        async def operation(client: redis.Redis) -> bool:
            if ttl:
                await client.setex(key, ttl, value)
            else:
                await client.set(key, value)
            return True

        return await self._with_client("SET", operation, False, key)

    async def set_if_revision_newer(
        self, key: str, value: str, revision: int, ttl: int
    ) -> bool:
        """Atomically keep the newest revision for a projection cache entry."""

        revision_key = f"{key}:__revision"
        script = """
        local current = redis.call('GET', KEYS[2])
        if current and tonumber(current) > tonumber(ARGV[1]) then
            return 0
        end
        redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[3])
        redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
        return 1
        """

        async def operation(client: redis.Redis) -> bool:
            result = await client.eval(
                script,
                2,
                key,
                revision_key,
                str(revision),
                value,
                str(ttl),
            )
            return bool(result)

        return await self._with_client("SET_IF_REVISION_NEWER", operation, False, key)

    async def delete(self, key: str) -> bool:
        """Delete a cached key."""

        async def operation(client: redis.Redis) -> bool:
            await client.delete(key)
            return True

        return await self._with_client("DELETE", operation, False, key)

    async def delete_pattern(self, pattern: str) -> int:
        """Delete keys matching a pattern using non-blocking SCAN.

        Keys are deleted in batches to avoid one Redis round-trip per match.
        `nutrition_bulk:*` invalidation otherwise dominates movement writes.
        """

        async def operation(client: redis.Redis) -> int:
            deleted = 0
            batch: list[str] = []

            async def flush() -> None:
                nonlocal deleted
                if not batch:
                    return
                await client.delete(*batch)
                deleted += len(batch)
                batch.clear()

            async for key in client.scan_iter(match=pattern):
                batch.append(key)
                if len(batch) >= 100:
                    await flush()
            await flush()
            if deleted:
                logger.debug("Deleted %d keys matching %s", deleted, pattern)
            return deleted

        return await self._with_client("delete_pattern", operation, 0, pattern)

    async def exists(self, key: str) -> bool:
        """Return True if a key exists."""

        async def operation(client: redis.Redis) -> bool:
            return bool(await client.exists(key))

        return await self._with_client("EXISTS", operation, False, key)

    async def set_if_not_exists(self, key: str, value: str, ttl: int) -> bool | None:
        """
        SET key value NX EX ttl.
        Returns True if set, False if key already existed, None if Redis unavailable.
        """

        async def _op(client: redis.Redis) -> str:
            result = await client.set(key, value, nx=True, ex=ttl)
            return "set" if result is not None else "exists"

        raw = await self._with_client("SET_NX", _op, "__nx_unavail__", key)
        if raw == "__nx_unavail__":
            return None
        return raw == "set"

    async def incr_with_expiry(self, key: str, ttl: int) -> int | None:
        """Atomically increment a bounded counter, returning None if unavailable."""

        async def operation(client: redis.Redis) -> int:
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, ttl)
            return count

        return await self._with_client("INCR", operation, None, key)
