"""Executes queued AI jobs: lease, run with a deadline, record the outcome.

Each lane runs one attempt at a time. Attempt outcomes:

- result: completed; the push goes out after a grace period unless the
  user has seen the result in the app by then.
- error: mapped to a client error code; transient errors go back to the
  queue with exponential backoff until attempts run out, then fail.
- cancel request: parse-text and chat attempts stop at once; a meal scan
  finishes and completes without a push, because a scan stopped while saving
  would keep its meal anyway.
- shutdown: parse-text and chat attempts stop and are re-queued; a meal scan
  keeps running until the app's shutdown timeout cancels it.
- lost lease: another worker owns the job now; nothing is written.

A scanned meal's save and its created event finish together even when the
attempt is cancelled by its deadline, a lost lease, or the shutdown timeout
(see ``run_to_completion``). Only a process that dies between the two can
keep the meal without its event.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from src.app.services.ai_jobs.error_mapping import INTERNAL_ERROR, map_job_exception
from src.app.services.ai_jobs.executors import AiJobExecutor, AiJobInterrupt
from src.app.services.ai_jobs.recorder import AiJobAttemptRecorder
from src.app.services.ai_jobs.signals import AiJobCancelSignals, AiJobWakeup
from src.app.services.ai_jobs.supervision import (
    AttemptOutcome,
    interrupt_work,
    run_detached,
    supervise_attempt,
)
from src.domain.exceptions.ai_job_exceptions import AiJobExecutionError
from src.domain.model.ai_jobs import AiJobClaim, AiJobType
from src.domain.model.ai_jobs.policies import classify_ai_job_error
from src.observability import distribution_metric

logger = logging.getLogger(__name__)

# Work that can stop at any await without leaving partial state behind.
INTERRUPTIBLE_AI_JOB_TYPES = frozenset({AiJobType.PARSE_TEXT, AiJobType.CHAT})
_INTERRUPT_GRACE_SECONDS = 5.0
_CLEANUP_TIMEOUT_SECONDS = 5.0


class AiJobWorker:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], Any],
        executors: Mapping[AiJobType, AiJobExecutor],
        cancel_signals: AiJobCancelSignals,
        wakeup: AiJobWakeup,
        concurrency: int,
        global_capacity: int,
        lease_seconds: int,
        heartbeat_seconds: float,
        deadline_seconds: float,
        poll_seconds: float,
        notify_grace_seconds: int,
        max_idle_seconds: float = 5.0,
        ready: Callable[[], Awaitable[bool]] | None = None,
        rng: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._uow_factory = uow_factory
        self._recorder = AiJobAttemptRecorder(
            uow_factory=uow_factory,
            lease_seconds=lease_seconds,
            heartbeat_seconds=heartbeat_seconds,
            notify_grace_seconds=notify_grace_seconds,
            rng=rng,
        )
        self._ready = ready or _always_ready
        self._executors = dict(executors)
        self._cancel_signals = cancel_signals
        self._wakeup = wakeup
        self._concurrency = max(1, concurrency)
        self._global_capacity = global_capacity
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._deadline_seconds = deadline_seconds
        self._poll_seconds = poll_seconds
        self._max_idle_seconds = max(poll_seconds, max_idle_seconds)
        self._clock = clock

    async def run(self, stop: asyncio.Event) -> None:
        """Run the lanes until ``stop`` is set and in-flight attempts end."""
        lanes = [
            asyncio.ensure_future(self._lane(index, stop))
            for index in range(self._concurrency)
        ]
        try:
            await asyncio.gather(*lanes)
        finally:
            for lane in lanes:
                lane.cancel()
            await asyncio.gather(*lanes, return_exceptions=True)

    async def _lane(self, index: int, stop: asyncio.Event) -> None:
        idle = self._poll_seconds
        while True:
            # Read before claiming: a job enqueued after this point bumps the
            # generation, so the wait below returns at once.
            seen = self._wakeup.generation
            if stop.is_set():
                return
            worked = False
            try:
                if await self._ready():
                    worked = await self.run_once(stop)
            except Exception:
                logger.exception("AI job lane %s failed to claim or run a job", index)
            if worked:
                idle = self._poll_seconds
                continue
            await self._wakeup.wait(idle, since=seen)
            idle = min(idle * 2, self._max_idle_seconds)

    async def run_once(self, stop: asyncio.Event | None = None) -> bool:
        """Run at most one attempt; return whether a job was claimed."""
        async with self._uow_factory() as uow:
            claim = await uow.ai_jobs.claim_next(
                lease_seconds=self._lease_seconds,
                global_capacity=self._global_capacity,
            )
        if claim is None:
            return False
        await self._process(claim, stop if stop is not None else asyncio.Event())
        return True

    async def _process(self, claim: AiJobClaim, stop: asyncio.Event) -> None:
        started = self._clock()
        cancel_event = self._cancel_signals.watch(claim.id)
        try:
            executor = self._executors.get(claim.job_type)
            if executor is None:
                logger.error("No executor for AI job type %s", claim.job_type)
                await self._recorder.fail(claim, classify_ai_job_error(INTERNAL_ERROR))
                return
            await self._run_attempt(claim, executor, cancel_event, stop, started)
        finally:
            self._cancel_signals.release(claim.id, cancel_event)
            distribution_metric(
                "ai_jobs.attempt.duration",
                self._clock() - started,
                unit="second",
                attributes={"job_type": claim.job_type.value},
            )

    async def _run_attempt(
        self,
        claim: AiJobClaim,
        executor: AiJobExecutor,
        cancel_event: asyncio.Event,
        stop: asyncio.Event,
        started: float,
    ) -> None:
        interrupt = AiJobInterrupt()
        work = asyncio.ensure_future(executor.execute(claim, interrupt))
        try:
            outcome = await supervise_attempt(
                work=work,
                heartbeat=lambda: self._recorder.heartbeat(claim),
                cancel_event=cancel_event,
                stop=stop,
                interruptible=claim.job_type in INTERRUPTIBLE_AI_JOB_TYPES,
                heartbeat_seconds=self._heartbeat_seconds,
                deadline=started + self._deadline_seconds,
                clock=self._clock,
            )
        except asyncio.CancelledError:
            # Hard shutdown: hand the job back if the lease still allows it.
            interrupt.code = "WORKER_INTERRUPTED"
            work.cancel()
            await run_detached(
                self._recorder.fail(claim, classify_ai_job_error("WORKER_INTERRUPTED")),
                timeout=_CLEANUP_TIMEOUT_SECONDS,
                description="record interrupted attempt",
            )
            raise
        except Exception:
            # The lease runs out and the job is picked up again.
            work.cancel()
            raise

        if outcome is AttemptOutcome.FINISHED:
            await self._record_finished(claim, work)
            return
        interrupt.code = {
            AttemptOutcome.CANCELLED: "CHAT_STOPPED",
            AttemptOutcome.STOPPED: "WORKER_INTERRUPTED",
            AttemptOutcome.TIMED_OUT: "AI_TIMEOUT",
        }.get(outcome)
        await interrupt_work(work, timeout=_INTERRUPT_GRACE_SECONDS)
        if outcome is AttemptOutcome.LOST_LEASE:
            self._recorder.lease_lost(claim)
        elif outcome is AttemptOutcome.CANCELLED:
            await self._recorder.cancel(claim)
        elif outcome is AttemptOutcome.STOPPED:
            await self._recorder.fail(
                claim, classify_ai_job_error("WORKER_INTERRUPTED")
            )
        else:
            await self._recorder.fail(claim, classify_ai_job_error("AI_TIMEOUT"))

    async def _record_finished(
        self, claim: AiJobClaim, work: asyncio.Future[Any]
    ) -> None:
        try:
            result = work.result()
        except asyncio.CancelledError:
            await self._recorder.fail(
                claim, classify_ai_job_error("WORKER_INTERRUPTED")
            )
            return
        except Exception as exc:
            await self._recorder.fail(claim, map_job_exception(exc))
            return
        if not isinstance(result, dict):
            logger.error(
                "AI job executor returned %s instead of a dict", type(result).__name__
            )
            await self._recorder.fail(
                claim, map_job_exception(AiJobExecutionError(INTERNAL_ERROR))
            )
            return
        await self._recorder.complete(claim, result)


async def _always_ready() -> bool:
    return True
