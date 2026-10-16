"""Entitlement ledger worker (NM-680, plan §6.3).

Runs only while the ``ent_reconcile`` flag is on. Each pass:

1. claims due inbox rows (``received``/``retry``, plus ``processing`` rows whose
   lease expired) with ``FOR UPDATE SKIP LOCKED`` and a lease, and re-syncs
   the users they concern (exponential backoff, ``dead`` after 10 attempts);
2. every ``periodic_interval_seconds``: recomputes projections whose
   ``next_change_at`` has passed (DB only) and re-syncs users with access
   whose last provider sync is older than ``resync_after``;
3. every ``sweep_interval_seconds``: requeues ``unmatched`` rows whose user
   now resolves;
4. every ``retention_interval_seconds``: nulls stored webhook bodies older
   than 90 days (D9), in batches.

Reads stay correct without this worker (the read-time rule recomputes
inline); it exists for retries, parked replays and drift.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from src.domain.model.billing.constants import (
    FLAG_ENT_RECONCILE,
    RAW_PAYLOAD_RETENTION_DAYS,
    SyncLane,
)
from src.domain.model.billing.ledger import BillingProviderEvent
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.feature_flag_reader_port import FeatureFlagReaderPort
from src.domain.utils.timezone_utils import utc_now
from src.observability import distribution_metric, increment_metric

logger = logging.getLogger(__name__)

RESYNC_REASON = "reconcile_stale"
# Extra rows listed to step past users whose re-sync is backing off.
MAX_RESYNC_BACKOFF_OVERFETCH = 1000


class InboxJobs(Protocol):
    """Inbox processing, composed from the app layer in ``src.bootstrap``."""

    async def process_claimed(
        self, event: BillingProviderEvent, *, lane: str
    ) -> str: ...

    async def sweep_unmatched(
        self, *, limit: int, after: tuple[datetime, str] | None = None
    ) -> tuple[int, tuple[datetime, str] | None]: ...


class EntitlementSyncer(Protocol):
    async def sync_user(self, user_id: str, lane: str, reason: str) -> Any: ...

    async def recompute_user(self, user_id: str) -> Any: ...


@dataclass
class WorkerPass:
    enabled: bool
    claimed: int = 0
    outcomes: Counter[str] = field(default_factory=Counter)
    recomputed: int = 0
    resynced: int = 0
    resync_errors: int = 0
    replayed: int = 0
    raw_purged: int = 0

    @property
    def worked(self) -> bool:
        return bool(
            self.claimed
            or self.recomputed
            or self.resynced
            or self.replayed
            or self.raw_purged
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "claimed": self.claimed,
            "outcomes": dict(self.outcomes),
            "recomputed": self.recomputed,
            "resynced": self.resynced,
            "resync_errors": self.resync_errors,
            "replayed": self.replayed,
            "raw_purged": self.raw_purged,
        }


class EntitlementWorker:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], AsyncUnitOfWorkPort],
        inbox: InboxJobs,
        syncer: EntitlementSyncer,
        flags: FeatureFlagReaderPort,
        batch_size: int = 10,
        periodic_batch_size: int = 50,
        lease_seconds: int = 600,
        poll_seconds: float = 5.0,
        periodic_interval_seconds: float = 60.0,
        sweep_interval_seconds: float = 900.0,
        retention_interval_seconds: float = 3600.0,
        retention_batch_size: int = 500,
        retention_max_batches: int = 20,
        resync_after: timedelta = timedelta(hours=24),
        resync_failure_backoff_seconds: float = 3600.0,
        worker_id: str | None = None,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not 1 <= batch_size <= 500
            or not 1 <= periodic_batch_size <= 1000
            or not 30 <= lease_seconds <= 3600
            or not 0.1 <= poll_seconds <= 300
            or not 1 <= retention_batch_size <= 5000
            or not 1 <= retention_max_batches <= 1000
        ):
            raise ValueError("Invalid entitlement worker budget")
        self._uow_factory = uow_factory
        self._inbox = inbox
        self._syncer = syncer
        self._flags = flags
        self._batch_size = batch_size
        self._periodic_batch_size = periodic_batch_size
        self._lease = timedelta(seconds=lease_seconds)
        self._poll_seconds = poll_seconds
        self._periodic_interval = periodic_interval_seconds
        self._sweep_interval = sweep_interval_seconds
        self._retention_interval = retention_interval_seconds
        self._retention_batch_size = retention_batch_size
        self._retention_max_batches = retention_max_batches
        self._resync_after = resync_after
        self._resync_failure_backoff = resync_failure_backoff_seconds
        # user_id -> monotonic time before which a failed re-sync is not retried.
        self._resync_backoff_until: dict[str, float] = {}
        self.worker_id = (worker_id or f"entitlement-worker:{uuid.uuid4()}")[:64]
        self._clock = clock
        self._monotonic = monotonic
        self._next_periodic_at: float | None = None
        self._next_sweep_at: float | None = None
        self._next_retention_at: float | None = None
        self._unmatched_cursor: tuple[datetime, str] | None = None

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                worked = (await self.run_once()).worked
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("entitlement worker pass failed")
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), self._poll_seconds)
                except TimeoutError:
                    pass

    async def run_once(self, *, force_periodic: bool = False) -> WorkerPass:
        """One pass. ``force_periodic`` runs the timed jobs now (``--once``)."""
        if not await self._flags.is_enabled(FLAG_ENT_RECONCILE):
            return WorkerPass(enabled=False)
        increment_metric("entitlement.worker.heartbeat")
        result = WorkerPass(enabled=True)
        await self._process_inbox(result)

        mono = self._monotonic()
        if force_periodic or self._due(self._next_periodic_at, mono):
            self._next_periodic_at = mono + self._periodic_interval
            await self._recompute_due(result)
            await self._resync_stale(result)
        if force_periodic or self._due(self._next_sweep_at, mono):
            self._next_sweep_at = mono + self._sweep_interval
            await self._sweep_unmatched(result)
        if force_periodic or self._due(self._next_retention_at, mono):
            self._next_retention_at = mono + self._retention_interval
            await self._purge_expired_raw(result)
        return result

    @staticmethod
    def _due(next_at: float | None, mono: float) -> bool:
        return next_at is None or mono >= next_at

    async def _process_inbox(self, result: WorkerPass) -> None:
        now = self._clock()
        async with self._uow_factory() as uow:
            claimed = await uow.billing_events.claim_due(
                now=now,
                limit=self._batch_size,
                lease_owner=self.worker_id,
                lease_expires_at=now + self._lease,
            )
        result.claimed = len(claimed)
        for event in claimed:
            outcome = await self._inbox.process_claimed(event, lane=SyncLane.RECONCILE)
            result.outcomes[outcome] += 1
            increment_metric(
                "entitlement.worker.event",
                attributes={"outcome": outcome, "event_type": event.event_type},
            )
            lag = (self._clock() - event.received_at).total_seconds()
            distribution_metric(
                "entitlement.inbox.lag", max(lag, 0.0) * 1000.0, unit="millisecond"
            )

    async def _recompute_due(self, result: WorkerPass) -> None:
        try:
            async with self._uow_factory() as uow:
                due = await uow.user_entitlements.list_due_for_recompute(
                    now=self._clock(), limit=self._periodic_batch_size
                )
        except Exception:
            logger.exception("entitlement worker could not list due projections")
            return
        for user_id in dict.fromkeys(user_id for user_id, _key in due):
            try:
                await self._syncer.recompute_user(user_id)
                result.recomputed += 1
            except Exception:
                logger.exception(
                    "entitlement recompute failed", extra={"user_id": user_id}
                )

    async def _resync_stale(self, result: WorkerPass) -> None:
        # A failed re-sync leaves the user stalest, so without a backoff the
        # same failing users would fill every batch.
        mono = self._monotonic()
        backoff = {
            user_id: until
            for user_id, until in self._resync_backoff_until.items()
            if until > mono
        }
        self._resync_backoff_until = backoff
        try:
            async with self._uow_factory() as uow:
                user_ids = await uow.user_entitlements.list_user_ids_for_resync(
                    synced_before=self._clock() - self._resync_after,
                    limit=self._periodic_batch_size
                    + min(len(backoff), MAX_RESYNC_BACKOFF_OVERFETCH),
                )
        except Exception:
            logger.exception("entitlement worker could not list stale users")
            return
        due = [user_id for user_id in user_ids if user_id not in backoff]
        for user_id in due[: self._periodic_batch_size]:
            try:
                sync = await self._syncer.sync_user(
                    user_id, SyncLane.RECONCILE, RESYNC_REASON
                )
            except Exception:
                logger.exception(
                    "entitlement re-sync failed", extra={"user_id": user_id}
                )
                sync = None
            if sync is None or getattr(sync, "sync_error", None):
                result.resync_errors += 1
                backoff[user_id] = self._monotonic() + self._resync_failure_backoff
            else:
                result.resynced += 1

    async def _sweep_unmatched(self, result: WorkerPass) -> None:
        try:
            replayed, self._unmatched_cursor = await self._inbox.sweep_unmatched(
                limit=self._periodic_batch_size, after=self._unmatched_cursor
            )
        except Exception:
            logger.exception("entitlement worker unmatched sweep failed")
            self._unmatched_cursor = None
            return
        result.replayed = replayed

    async def _purge_expired_raw(self, result: WorkerPass) -> None:
        """D9: stored webhook bodies are kept 90 days. Short transactions;
        whatever one pass leaves is picked up by the next."""
        cutoff = self._clock() - timedelta(days=RAW_PAYLOAD_RETENTION_DAYS)
        try:
            for _ in range(self._retention_max_batches):
                async with self._uow_factory() as uow:
                    purged = await uow.billing_events.purge_raw_received_before(
                        cutoff, limit=self._retention_batch_size
                    )
                result.raw_purged += purged
                if purged < self._retention_batch_size:
                    break
        except Exception:
            logger.exception("entitlement worker raw retention purge failed")
        if result.raw_purged:
            increment_metric("entitlement.inbox.raw_purged", value=result.raw_purged)
