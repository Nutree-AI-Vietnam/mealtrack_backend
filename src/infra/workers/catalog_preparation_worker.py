"""Dedicated SQL-polling worker with bounded admission and fenced publication."""

import asyncio
import logging
from time import perf_counter

from src.domain.ports.catalog_preparation_port import (
    PreparationOutcome,
    PreparationResult,
    PreparationTask,
)
from src.infra.repositories.catalog_preparation_repository import (
    AsyncCatalogPreparationRepository,
)
from src.infra.workers.catalog_preparation_metrics import (
    record_claim,
    record_completion,
)

logger = logging.getLogger(__name__)


class CatalogPreparationWorker:
    def __init__(
        self,
        session_factory,
        computer,
        *,
        concurrency=2,
        global_capacity=4,
        lease_seconds=120,
        provider_deadline_seconds=60,
        poll_seconds=2,
        locales=("vi",),
    ):
        if (
            not 1 <= concurrency <= global_capacity <= 64
            or not 10 <= lease_seconds <= 3600
            or not 0 < provider_deadline_seconds < lease_seconds
            or not 0.1 <= poll_seconds <= 60
        ):
            raise ValueError("Invalid catalog worker resource budget")
        self.session_factory, self.computer = session_factory, computer
        self.concurrency, self.global_capacity = concurrency, global_capacity
        self.lease_seconds, self.provider_deadline_seconds = (
            lease_seconds,
            provider_deadline_seconds,
        )
        self.poll_seconds, self.locales = poll_seconds, tuple(locales)

    async def run(self, stop: asyncio.Event):
        # Each loop claims only when its provider lane is free. Periodic SQL
        # polling repairs lost optional wakeups and process restarts.
        await asyncio.gather(*(self._lane(stop) for _ in range(self.concurrency)))

    async def _lane(self, stop):
        while not stop.is_set():
            try:
                worked = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # The committed lease remains the recovery authority. Do not
                # hide computation failures or publish a fabricated result.
                logger.exception("catalog preparation iteration failed")
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), self.poll_seconds)
                except TimeoutError:
                    pass

    async def run_once(self):
        async with self.session_factory() as session:
            claim = await AsyncCatalogPreparationRepository(session).claim_next(
                lease_seconds=self.lease_seconds, global_capacity=self.global_capacity
            )
            await session.commit()
        if claim is None:
            return False
        started = perf_counter()
        record_claim(claim)
        if claim.task == PreparationTask.PROJECTION:
            async with self.session_factory() as session:
                committed = await AsyncCatalogPreparationRepository(
                    session
                ).reconcile_projection(claim, locales=self.locales)
                await session.commit()
            record_completion(
                claim, "reconciled" if committed else "fenced", perf_counter() - started
            )
            return True
        async with self.session_factory() as session:
            preparation = await AsyncCatalogPreparationRepository(session).load_input(
                claim
            )
            await session.commit()
        if preparation is None:
            result = PreparationResult(
                PreparationOutcome.SUPERSEDED, error_code="input_changed"
            )
        else:
            # Snapshot acquisition can consume most of the original lease.
            # Renew before provider admission, then use periodic heartbeats.
            async with self.session_factory() as session:
                renewed = await AsyncCatalogPreparationRepository(session).heartbeat(
                    claim, lease_seconds=self.lease_seconds
                )
                await session.commit()
            if not renewed:
                record_completion(claim, "fenced", perf_counter() - started)
                return True
            finished, lost_lease = asyncio.Event(), asyncio.Event()
            heartbeat = asyncio.create_task(
                self._heartbeat(claim, finished, lost_lease)
            )
            computation = asyncio.create_task(self._compute(preparation))
            lease_wait = asyncio.create_task(lost_lease.wait())
            try:
                done, _ = await asyncio.wait(
                    (computation, lease_wait), return_when=asyncio.FIRST_COMPLETED
                )
                if lease_wait in done:
                    record_completion(claim, "fenced", perf_counter() - started)
                    return True
                result = computation.result()
            finally:
                finished.set()
                for task in (heartbeat, computation, lease_wait):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(
                    heartbeat, computation, lease_wait, return_exceptions=True
                )
        async with self.session_factory() as session:
            committed = await AsyncCatalogPreparationRepository(session).complete(
                claim, result
            )
            await session.commit()
        record_completion(
            claim,
            result.outcome.value if committed else "fenced",
            perf_counter() - started,
        )
        return True

    async def _compute(self, preparation):
        try:
            async with asyncio.timeout(self.provider_deadline_seconds):
                return await self.computer.compute(preparation)
        except TimeoutError:
            return PreparationResult(
                PreparationOutcome.RETRYABLE_FAILURE, error_code="provider_deadline"
            )
        except Exception:
            return PreparationResult(
                PreparationOutcome.RETRYABLE_FAILURE, error_code="provider_unavailable"
            )

    async def _heartbeat(self, claim, finished, lost_lease):
        while not finished.is_set():
            try:
                await asyncio.wait_for(finished.wait(), min(20, self.lease_seconds / 3))
                return
            except TimeoutError:
                pass
            try:
                async with self.session_factory() as session:
                    renewed = await AsyncCatalogPreparationRepository(
                        session
                    ).heartbeat(claim, lease_seconds=self.lease_seconds)
                    await session.commit()
                if not renewed:
                    lost_lease.set()
                    return
            except Exception:
                # Losing renewal cancels provider work; stale publication still
                # fails the database token/expiry fence after recovery.
                lost_lease.set()
                return
