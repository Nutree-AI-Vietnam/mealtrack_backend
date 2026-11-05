"""Append integration events to the Redis stream the nutreeai_async Worker reads.

The stream lives in the same Redis (`REDIS_URL`) the Worker already reaches
through Upstash REST. Each entry holds one compact JSON envelope under the
`event` field. `MAXLEN ~` bounds memory while the Worker is down: past the
cap, the oldest entries are trimmed, delivered or not.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import redis.asyncio as redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

STREAM_KEY_PREFIX = "nutreeai_async:events"
EVENT_FIELD = "event"

_MAX_CONNECTIONS = 10
# A burst of writes waits this long for a free connection instead of failing.
_POOL_WAIT_SECONDS = 10.0
_HEALTH_CHECK_INTERVAL_SECONDS = 30


class RedisStreamPublishError(Exception):
    """Raised when Redis does not accept an integration event."""


def stream_key(environment: str) -> str:
    """Return the environment's stream key; the Worker reads the same name."""
    return f"{STREAM_KEY_PREFIX}:{environment}"


class RedisStreamEventPublisher:
    """XADD one JSON event to the environment stream, trimming old entries."""

    def __init__(
        self,
        *,
        redis_url: str,
        stream_key: str,
        max_length: int,
        timeout_seconds: float = 3.0,
        client_factory: Callable[[], redis.Redis] | None = None,
    ) -> None:
        self._redis_url = redis_url
        self._stream_key = stream_key
        self._max_length = max_length
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory or self._connect
        self._client: redis.Redis | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def from_settings(cls) -> RedisStreamEventPublisher:
        from src.infra.config.settings import get_settings

        settings = get_settings()
        return cls(
            redis_url=settings.redis_url,
            stream_key=stream_key(settings.ENVIRONMENT),
            max_length=settings.INTEGRATION_EVENT_STREAM_MAXLEN,
        )

    @property
    def stream_key(self) -> str:
        """Redis key of the stream this publisher appends to."""
        return self._stream_key

    def _connect(self) -> redis.Redis:
        # The default pool raises MaxConnectionsError as soon as every
        # connection is busy, which would drop events during a write burst.
        pool = redis.BlockingConnectionPool.from_url(
            self._redis_url,
            max_connections=_MAX_CONNECTIONS,
            timeout=_POOL_WAIT_SECONDS,
            decode_responses=True,
            socket_timeout=self._timeout_seconds,
            socket_connect_timeout=self._timeout_seconds,
            health_check_interval=_HEALTH_CHECK_INTERVAL_SECONDS,
        )
        return redis.Redis.from_pool(pool)

    def _client_for_running_loop(self) -> redis.Redis:
        # redis.asyncio connections belong to the loop that opened them. Hold
        # the loop itself: a closed loop's id() can be reused by the next one.
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop:
            self._client = self._client_factory()
            self._loop = loop
        return self._client

    async def publish(self, payload: dict[str, Any]) -> None:
        """Append payload to the stream or raise when Redis does not accept it."""
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        try:
            await self._client_for_running_loop().xadd(
                self._stream_key,
                {EVENT_FIELD: body},
                maxlen=self._max_length,
                approximate=True,
            )
        except (RedisError, OSError) as exc:
            raise RedisStreamPublishError(
                f"Redis stream rejected event: {type(exc).__name__}"
            ) from exc

    async def aclose(self) -> None:
        """Close this publisher's Redis connections."""
        client, self._client, self._loop = self._client, None, None
        if client is None:
            return
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 - shutdown must continue
            logger.warning("redis stream publisher close failed", exc_info=True)
