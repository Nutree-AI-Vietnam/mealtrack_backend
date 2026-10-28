"""
GetStreakQueryHandler — the logging streak and its restore state.
"""

from __future__ import annotations

import logging
from typing import Any

from src.app.events.base import EventHandler, handles
from src.app.queries.meal.get_streak_query import GetStreakQuery
from src.app.services.streak_restore_state import (
    MIN_CACHE_TTL,
    RestoreState,
    load_restore_state,
    streak_cache_ttl,
    streak_payload,
)
from src.domain.cache.cache_keys import CacheKeys
from src.domain.model.streak import StreakRestorePolicy
from src.domain.ports.cache_port import CachePort
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)

_REVISION_FIELD = "intake_revision"


@handles(GetStreakQuery)
class GetStreakQueryHandler(EventHandler[GetStreakQuery, dict[str, Any]]):
    """Current and best streak, restored days and any restore offer."""

    def __init__(
        self,
        cache_service: CachePort | None = None,
        policy: StreakRestorePolicy | None = None,
    ):
        self.cache_service = cache_service
        self.policy = policy or StreakRestorePolicy()

    async def handle(self, query: GetStreakQuery) -> dict[str, Any]:
        cache = self.cache_service
        if cache is None:
            return streak_payload(await self._load(query), self.policy)

        key, _ = CacheKeys.user_streak(query.user_id)
        # Read the fence before computing: a write that lands mid-compute
        # bumps past it, and the stale result is never stored over it.
        cached, revision = await cache.get_json_with_revision(
            key, CacheKeys.intake_revision(query.user_id)[0]
        )
        if revision is not None and cached and cached.get(_REVISION_FIELD) == revision:
            return {k: v for k, v in cached.items() if k != _REVISION_FIELD}

        state = await self._load(query)
        payload = streak_payload(state, self.policy)
        ttl = streak_cache_ttl(state.timezone, state.computed_at)
        # Near midnight a cached payload would outlive the day it describes.
        if revision is not None and ttl >= MIN_CACHE_TTL:
            await cache.set_json(  # type: ignore[attr-defined]
                key,
                {**payload, _REVISION_FIELD: revision},
                ttl,
                revision_field=_REVISION_FIELD,
            )
        return payload

    async def _load(self, query: GetStreakQuery) -> RestoreState:
        async with AsyncUnitOfWork() as uow:
            return await load_restore_state(
                uow, query.user_id, query.header_timezone, self.policy
            )
