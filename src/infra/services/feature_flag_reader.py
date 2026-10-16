"""Server-side reader for the DB-backed ``feature_flags`` table (NM-680 kill switches).

All flags are read in one query and cached in-process for ``ttl_seconds``
(30 s by default), so a flag flip takes effect within the TTL on every worker
without Redis. Read errors never propagate: the caller gets its default.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from src.domain.ports.feature_flag_reader_port import FeatureFlagReaderPort
from src.infra.database.models.feature_flag import FeatureFlag

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 30.0
# After a failed read, serve defaults for this long instead of retrying per call.
DEFAULT_FAILURE_BACKOFF_SECONDS = 5.0


class FeatureFlagReader(FeatureFlagReaderPort):
    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        failure_backoff_seconds: float = DEFAULT_FAILURE_BACKOFF_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._ttl = ttl_seconds
        self._failure_backoff = failure_backoff_seconds
        self._clock = clock
        self._flags: dict[str, bool] | None = None
        self._expires_at: float | None = None

    async def is_enabled(self, name: str, default: bool = False) -> bool:
        flags = await self._snapshot()
        if flags is None or name not in flags:
            return default
        return flags[name]

    def invalidate(self) -> None:
        """Force the next read to hit the database."""
        self._expires_at = None

    async def _snapshot(self) -> dict[str, bool] | None:
        now = self._clock()
        if self._expires_at is not None and now < self._expires_at:
            return self._flags
        try:
            async with self._session_factory() as session:
                rows = await session.execute(
                    select(FeatureFlag.name, FeatureFlag.enabled)
                )
                flags = {name: bool(enabled) for name, enabled in rows}
        except Exception:
            logger.warning(
                "Feature flag read failed; using defaults for %.0fs",
                self._failure_backoff,
                exc_info=True,
            )
            self._flags = None
            self._expires_at = now + self._failure_backoff
            return None
        self._flags = flags
        self._expires_at = now + self._ttl
        return flags
