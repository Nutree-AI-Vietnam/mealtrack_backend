"""Writes the outcome of one AI job attempt, fenced on the attempt's claim.

Shared by the worker and the in-process chat runner, so both apply the same
retry policy and report the same metrics.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from typing import Any

from src.domain.model.ai_jobs import (
    AiJobClaim,
    AiJobError,
    AiJobHeartbeat,
    AiJobStatus,
)
from src.domain.model.ai_jobs.policies import retry_backoff_seconds, should_auto_retry
from src.observability import increment_metric

logger = logging.getLogger(__name__)


class AiJobAttemptRecorder:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], Any],
        lease_seconds: int,
        heartbeat_seconds: float,
        notify_grace_seconds: int,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._uow_factory = uow_factory
        self._lease_seconds = lease_seconds
        # A hung database call must not hold the attempt past its lease.
        self._heartbeat_timeout = max(5.0, heartbeat_seconds * 2)
        self._notify_grace_seconds = notify_grace_seconds
        self._rng = rng

    async def heartbeat(self, claim: AiJobClaim) -> AiJobHeartbeat | None:
        """Extend the lease; None when the database did not answer in time."""
        try:
            async with asyncio.timeout(self._heartbeat_timeout):
                async with self._uow_factory() as uow:
                    return await uow.ai_jobs.heartbeat(
                        claim, lease_seconds=self._lease_seconds
                    )
        except Exception:
            logger.warning(
                "AI job heartbeat failed", extra={"job_id": claim.id}, exc_info=True
            )
            return None

    async def complete(self, claim: AiJobClaim, result: dict[str, Any]) -> bool:
        """Store the result; False when another attempt owns the job now."""
        async with self._uow_factory() as uow:
            owned = await uow.ai_jobs.complete(
                claim, result=result, notify_delay_seconds=self._notify_grace_seconds
            )
        count_attempt(claim, "completed" if owned else "lost_lease")
        return bool(owned)

    async def fail(self, claim: AiJobClaim, error: AiJobError) -> AiJobStatus | None:
        """Record a failed attempt and return the job's new status.

        Transient errors go back to the queue with backoff while attempts
        remain. Returns None when another attempt owns the job now.
        """
        retry_delay = (
            retry_backoff_seconds(claim.attempts, rng=self._rng)
            if should_auto_retry(
                error, attempts=claim.attempts, max_attempts=claim.max_attempts
            )
            else None
        )
        async with self._uow_factory() as uow:
            status = await uow.ai_jobs.fail_attempt(
                claim,
                error=error,
                retry_delay_seconds=retry_delay,
                notify_delay_seconds=self._notify_grace_seconds,
            )
        logger.warning(
            "AI job attempt failed",
            extra={
                "job_id": claim.id,
                "job_type": claim.job_type.value,
                "attempt": claim.attempts,
                "error_code": error.code,
                "next_status": status.value if status else None,
            },
        )
        count_attempt(
            claim, status.value if status else "lost_lease", error_code=error.code
        )
        return status

    async def cancel(self, claim: AiJobClaim) -> bool:
        """Finish a cancelled attempt; False when another attempt owns the job."""
        async with self._uow_factory() as uow:
            owned = await uow.ai_jobs.cancel_claimed(claim)
        count_attempt(claim, "cancelled" if owned else "lost_lease")
        return bool(owned)

    async def current_status(self, claim: AiJobClaim) -> AiJobStatus | None:
        """Best effort: the job's status now, or None if it cannot be read."""
        try:
            async with self._uow_factory() as uow:
                job = await uow.ai_jobs.get(user_id=claim.user_id, job_id=claim.id)
        except Exception:
            logger.warning(
                "AI job status lookup failed", extra={"job_id": claim.id}, exc_info=True
            )
            return None
        return job.status if job is not None else None

    @staticmethod
    def lease_lost(claim: AiJobClaim) -> None:
        logger.warning(
            "AI job lease lost mid-attempt",
            extra={"job_id": claim.id, "job_type": claim.job_type.value},
        )
        count_attempt(claim, "lost_lease")


def count_attempt(
    claim: AiJobClaim, outcome: str, *, error_code: str | None = None
) -> None:
    attributes = {"job_type": claim.job_type.value, "outcome": outcome}
    if error_code:
        attributes["error_code"] = error_code
    increment_metric("ai_jobs.attempt", attributes=attributes)
