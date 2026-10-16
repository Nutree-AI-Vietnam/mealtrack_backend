"""Entitlement answers for client-facing endpoints (NM-680, plan §6.2 and §7.1).

Backs ``GET/POST /v1/me/entitlements`` and the entitlement hooks of
``/v1/users/sync`` and ``GET /v1/users/firebase/{uid}``. Web-funnel finalize
has none: the app's hinted ``/sync`` (``redeem``) fetches after it, so a second
purchase-lane fetch while finalize holds its row locks would only add latency.
Every hook is dark-launched: with the ``ent_*`` flags off it returns the RC
authority body or the caller's legacy value and touches nothing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from src.app.schemas.entitlement_schemas import EntitlementsView
from src.app.services.entitlement_inbox_service import EntitlementInboxService
from src.app.services.entitlement_query_service import EntitlementQueryService
from src.app.services.entitlement_sync_service import (
    EntitlementSyncResult,
    EntitlementSyncService,
)
from src.domain.model.billing.constants import (
    ENTITLEMENT_STANDARD,
    FLAG_ENT_LEDGER_WRITE,
    FLAG_ENT_RECONCILE,
    SyncLane,
)
from src.domain.ports.feature_flag_reader_port import FeatureFlagReaderPort
from src.domain.utils.timezone_utils import utc_now
from src.observability import increment_metric

logger = logging.getLogger(__name__)

CLIENT_HINT_KINDS = frozenset({"purchase", "restore", "redeem"})
# A "no" within this window of a purchase hint is never cached (plan §6.2).
PURCHASE_HINT_WINDOW = timedelta(minutes=15)
# Unhinted /sync re-fetches only when the last provider sync is older than this.
UNHINTED_REFRESH_AFTER = timedelta(minutes=15)
# Per user and minute: provider fetches by unhinted /sync (answers from a fresh
# projection are free), and hinted fetches on the top-priority purchase lane.
SYNC_RATE_LIMIT = 6
HINTED_SYNC_LIMIT = 2
SYNC_RATE_WINDOW = timedelta(minutes=1)
# Re-syncs after a hinted /sync, in seconds after it. RC may serve a stale
# customer for a moment after a purchase.
HINT_FOLLOWUP_DELAYS: tuple[float, ...] = (5.0, 30.0)
# At most one follow-up chain per user in this window. Follow-ups run below the
# webhook lane, so hint spam cannot starve webhook syncs.
HINT_FOLLOWUP_COOLDOWN_SECONDS = 120.0
FOLLOWUP_LANE = SyncLane.LOGIN_SYNC
FOLLOWUP_BUDGET_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class ClientHint:
    """``client_hint`` of ``POST /v1/me/entitlements/sync``: logged, never trusted."""

    kind: str
    product_id: str | None = None
    transaction_id: str | None = None


class SlidingWindowRateLimiter:
    """In-process per-key limit. Over the limit the caller answers without a fetch.

    Per process, like the slowapi default storage: with N workers a user can
    reach N x ``limit`` fetches per window; the shared RC budget caps the rest.
    """

    def __init__(
        self,
        limit: int = SYNC_RATE_LIMIT,
        window: timedelta = SYNC_RATE_WINDOW,
        *,
        max_keys: int = 10_000,
    ) -> None:
        self._limit = limit
        self._window = window
        self._max_keys = max_keys
        self._hits: dict[str, deque[datetime]] = {}

    def allow(self, key: str, now: datetime) -> bool:
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self._max_keys:
                self._prune(now)
            hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True

    def _prune(self, now: datetime) -> None:
        for key in [
            key
            for key, hits in self._hits.items()
            if not hits or now - hits[-1] >= self._window
        ]:
            del self._hits[key]
        if len(self._hits) >= self._max_keys:
            # Still full of active keys: forget the oldest half.
            for key in list(self._hits)[: self._max_keys // 2]:
                del self._hits[key]


class DelayedResyncScheduler:
    """In-process follow-up re-syncs after a purchase hint (+5 s, +30 s).

    One chain per user at a time, and at most one chain start per user per
    ``cooldown_seconds``: further hints neither restart nor add a chain. Tasks
    are lost on a restart; the webhook and the worker's 24 h re-sync cover that.
    """

    def __init__(
        self,
        run: Callable[[str], Awaitable[Any]],
        *,
        delays: tuple[float, ...] = HINT_FOLLOWUP_DELAYS,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        cooldown_seconds: float = HINT_FOLLOWUP_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self._run = run
        self._delays = tuple(sorted(delays))
        self._sleep = sleep
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._pending: dict[str, asyncio.Task[None]] = {}
        self._started: dict[str, float] = {}

    def schedule(self, user_id: str) -> asyncio.Task[None] | None:
        if not self._delays:
            return None
        now = self._clock()
        running = self._pending.get(user_id)
        started = self._started.get(user_id)
        if (running is not None and not running.done()) or (
            started is not None and now - started < self._cooldown
        ):
            increment_metric("entitlement.sync.followup_skipped")
            return None
        if len(self._started) >= self._max_keys:
            self._prune(now)
        self._started[user_id] = now
        task = asyncio.get_running_loop().create_task(self._chain(user_id))
        self._pending[user_id] = task
        task.add_done_callback(lambda done: self._forget(user_id, done))
        return task

    def _prune(self, now: float) -> None:
        for key in [
            key
            for key, started in self._started.items()
            if now - started >= self._cooldown and key not in self._pending
        ]:
            del self._started[key]

    def pending(self) -> int:
        return len(self._pending)

    def _forget(self, user_id: str, task: asyncio.Task[None]) -> None:
        if self._pending.get(user_id) is task:
            del self._pending[user_id]

    async def _chain(self, user_id: str) -> None:
        elapsed = 0.0
        for delay in self._delays:
            await self._sleep(max(delay - elapsed, 0.0))
            elapsed = delay
            try:
                await self._run(user_id)
            except Exception:
                logger.warning(
                    "Follow-up entitlement re-sync failed",
                    extra={"user_id": user_id},
                    exc_info=True,
                )


class EntitlementAccountService:
    def __init__(
        self,
        *,
        query_service: EntitlementQueryService,
        sync_service: EntitlementSyncService,
        inbox_service: EntitlementInboxService,
        flags: FeatureFlagReaderPort,
        scheduler: DelayedResyncScheduler,
        rate_limiter: SlidingWindowRateLimiter,
        hint_rate_limiter: SlidingWindowRateLimiter,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._query = query_service
        self._sync = sync_service
        self._inbox = inbox_service
        self._flags = flags
        self._scheduler = scheduler
        self._limiter = rate_limiter
        self._hint_limiter = hint_rate_limiter
        self._clock = clock

    async def get_entitlements(self, user_id: str) -> EntitlementsView:
        """``GET /v1/me/entitlements``: the projection with the read-time rule."""
        return await self._query.get_entitlements(user_id, now=self._clock())

    async def sync_entitlements(
        self, user_id: str, hint: ClientHint | None = None
    ) -> EntitlementsView:
        """``POST /v1/me/entitlements/sync``.

        Hinted: re-fetch and record the hint, so a "no" is never cached. The
        first ``HINTED_SYNC_LIMIT`` per minute use the purchase lane and start
        the follow-ups; later ones fall back to the unhinted budget and lane.
        Unhinted: re-fetch only when the last provider sync is older than
        15 min, at most ``SYNC_RATE_LIMIT`` fetches per minute; otherwise the
        current projection. With RC the authority, nothing is fetched at all.
        """
        now = self._clock()
        if not await self._query.backend_is_authority():
            return EntitlementsView.rc_authority(user_id, now)
        if hint is not None:
            return await self._hinted_sync(user_id, hint, now)

        projections = await self._query.current_projections(user_id, now=now)
        standard = next(
            (row for row in projections if row.entitlement_key == ENTITLEMENT_STANDARD),
            None,
        )
        if (
            standard is not None
            and standard.last_provider_sync_at is not None
            and now - standard.last_provider_sync_at < UNHINTED_REFRESH_AFTER
        ):
            return EntitlementsView.from_projections(user_id, projections, now)
        if not self._limiter.allow(user_id, now):
            increment_metric(
                "entitlement.sync.rate_limited", attributes={"hinted": "false"}
            )
            return EntitlementsView.from_projections(user_id, projections, now)
        result = await self._sync.sync_user(user_id, SyncLane.LOGIN_SYNC, "client_sync")
        return await self._query.view_for_sync(result, now=self._clock())

    async def _hinted_sync(
        self, user_id: str, hint: ClientHint, now: datetime
    ) -> EntitlementsView:
        logger.info(
            "Purchase-hinted entitlement sync",
            extra={
                "user_id": user_id,
                "hint_kind": hint.kind,
                "hint_product_id": hint.product_id,
                "hint_transaction_id": hint.transaction_id,
            },
        )
        reason = f"client_hint:{hint.kind}"
        if self._hint_limiter.allow(user_id, now):
            result = await self._sync.sync_user(
                user_id, SyncLane.PURCHASE_SYNC, reason, purchase_hint=True
            )
            self._scheduler.schedule(user_id)
            return await self._query.view_for_sync(result, now=self._clock())

        # The hint is untrusted: past its own budget it gets no priority and
        # no follow-ups, but it still fetches when the ordinary budget allows
        # and is always recorded.
        increment_metric("entitlement.sync.rate_limited", attributes={"hinted": "true"})
        if self._limiter.allow(user_id, now):
            result = await self._sync.sync_user(
                user_id, SyncLane.LOGIN_SYNC, reason, purchase_hint=True
            )
        else:
            result = EntitlementSyncResult(
                user_id=user_id,
                projections=await self._sync.record_purchase_hint(user_id),
            )
        return await self._query.view_for_sync(result, now=self._clock())

    async def follow_up_sync(self, user_id: str) -> None:
        """Delayed re-sync after a purchase hint (run by the scheduler)."""
        await self._sync.sync_user(
            user_id,
            FOLLOWUP_LANE,
            "purchase_followup",
            budget_timeout=FOLLOWUP_BUDGET_TIMEOUT_SECONDS,
        )

    async def after_user_sync(self, user_id: str, firebase_uid: str | None) -> None:
        """``/v1/users/sync`` hook: seed the primary identity, replay parked rows.

        Runs only while the ledger is being written or reconciled. Never raises.
        """
        try:
            ledger = await self._flags.is_enabled(FLAG_ENT_LEDGER_WRITE)
            reconcile = await self._flags.is_enabled(FLAG_ENT_RECONCILE)
            if not (ledger or reconcile) or not firebase_uid:
                return
            await self._inbox.link_primary_identity(user_id, firebase_uid)
            if reconcile:
                await self._inbox.replay_unmatched_for_user(user_id)
        except Exception:
            increment_metric("entitlement.user_sync_hook.failed")
            logger.warning(
                "Entitlement hook after user sync failed",
                extra={"user_id": user_id},
                exc_info=True,
            )

    async def has_subscription(self, user_id: str, *, legacy: bool) -> bool:
        """``has_subscription`` for the user endpoints.

        From the projection while the backend is the authority; ``legacy``
        otherwise, when the ledger cannot say (never synced), or on any error.
        """
        try:
            if not await self._query.backend_is_authority():
                return legacy
            verdict = await self._query.has_access(user_id, now=self._clock())
        except Exception:
            increment_metric("entitlement.read.failed", attributes={"caller": "users"})
            logger.warning(
                "Projection read failed; using the legacy subscription flag",
                extra={"user_id": user_id},
                exc_info=True,
            )
            return legacy
        return legacy if verdict is None else verdict
