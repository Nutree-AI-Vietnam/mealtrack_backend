"""Entitlement reads: the only reader of ``user_entitlements`` (NM-680, plan §4 item 3).

Applies the read-time rule: the stored ``has_access`` is a cache, so a row
whose ``next_change_at`` has passed is recomputed inline from
``store_subscriptions`` and grants (DB only, no provider call) before answering.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import datetime

from src.app.schemas.entitlement_schemas import (
    EntitlementDebugView,
    EntitlementsView,
)
from src.app.services.entitlement_sync_service import (
    EntitlementSyncResult,
    EntitlementSyncService,
)
from src.domain.model.billing.constants import (
    ENTITLEMENT_STANDARD,
    FLAG_ENT_READ_API,
    FLAG_ENT_READ_API_DEFER,
)
from src.domain.model.billing.ledger import UserEntitlement
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.feature_flag_reader_port import FeatureFlagReaderPort
from src.domain.services.billing.access_policy import (
    effective_has_access,
    needs_recompute,
)
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)

DEBUG_EVENT_LIMIT = 20


class EntitlementQueryService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], AsyncUnitOfWorkPort],
        sync_service: EntitlementSyncService,
        flags: FeatureFlagReaderPort,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._uow_factory = uow_factory
        self._sync = sync_service
        self._flags = flags
        self._clock = clock

    async def backend_is_authority(self) -> bool:
        """``ent_read_api`` on and the ``ent_read_api_defer`` kill switch off."""
        if await self._flags.is_enabled(FLAG_ENT_READ_API_DEFER):
            return False
        return await self._flags.is_enabled(FLAG_ENT_READ_API)

    async def get_entitlements(
        self, user_id: str, *, now: datetime | None = None
    ) -> EntitlementsView:
        """Body of ``GET /v1/me/entitlements``."""
        now = now or self._clock()
        if not await self.backend_is_authority():
            return EntitlementsView.rc_authority(user_id, now)
        projections = await self.current_projections(user_id, now=now)
        return EntitlementsView.from_projections(user_id, projections, now)

    async def view_for_sync(
        self, result: EntitlementSyncResult, *, now: datetime | None = None
    ) -> EntitlementsView:
        """Body of ``POST /v1/me/entitlements/sync`` after ``sync_user``."""
        now = now or self._clock()
        if not await self.backend_is_authority():
            return EntitlementsView.rc_authority(
                result.user_id, now, stale=result.stale, sync_error=result.sync_error
            )
        projections: Iterable[UserEntitlement] = result.projections.values()
        if any(needs_recompute(row, now) for row in projections):
            projections = (
                await self._sync.recompute_user(result.user_id, now=now)
            ).values()
        return EntitlementsView.from_projections(
            result.user_id,
            projections,
            now,
            stale=result.stale,
            sync_error=result.sync_error,
        )

    async def current_projections(
        self, user_id: str, *, now: datetime | None = None
    ) -> list[UserEntitlement]:
        """Projection rows with the read-time rule applied; flag independent."""
        now = now or self._clock()
        async with self._uow_factory() as uow:
            rows = await uow.user_entitlements.list_for_user(user_id)
        if any(needs_recompute(row, now) for row in rows):
            recomputed = await self._sync.recompute_user(user_id, now=now)
            return [recomputed[key] for key in sorted(recomputed)]
        return rows

    async def has_access(
        self,
        user_id: str,
        entitlement_key: str = ENTITLEMENT_STANDARD,
        *,
        now: datetime | None = None,
    ) -> bool | None:
        """Ledger verdict for server readers.

        None when the ledger cannot say "no": no row, or no access and the user
        was never synced with the provider (same rule as the ``unknown`` state).
        """
        now = now or self._clock()
        for row in await self.current_projections(user_id, now=now):
            if row.entitlement_key == entitlement_key:
                active = effective_has_access(row, now)
                if not active and row.last_provider_sync_at is None:
                    return None
                return active
        return None

    async def get_debug_view(
        self, user_id: str, *, event_limit: int = DEBUG_EVENT_LIMIT
    ) -> EntitlementDebugView:
        """Admin view: projection, store rows, grants and the latest inbox rows."""
        async with self._uow_factory() as uow:
            return EntitlementDebugView(
                user_id=user_id,
                projections=await uow.user_entitlements.list_for_user(user_id),
                store_subscriptions=await uow.store_subscriptions.list_for_user(
                    user_id
                ),
                grants=await uow.entitlement_grants.list_for_user(user_id),
                events=await uow.billing_events.list_for_user(
                    user_id, limit=event_limit
                ),
            )
