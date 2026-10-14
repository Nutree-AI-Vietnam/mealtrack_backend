"""Persistence port for background AI jobs and their delivery state."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from src.domain.model.ai_jobs import (
    AiJob,
    AiJobClaim,
    AiJobError,
    AiJobHeartbeat,
    AiJobListState,
    AiJobNotificationClaim,
    AiJobStatus,
    NewAiJob,
)


class AiJobRepositoryPort(ABC):
    """Owns the job lifecycle: enqueue, lease-based claims, and completion.

    Worker-side writes are fenced on the claim token and an unexpired lease,
    so an attempt that lost its lease can never overwrite a newer one.
    Times are taken from the database clock.
    """

    # --- client-facing ---

    @abstractmethod
    async def schema_ready(self) -> bool:
        """Return whether the job and push-token storage is migrated."""

    @abstractmethod
    async def create(self, job: NewAiJob) -> tuple[AiJob, bool]:
        """Insert a queued job; if the id exists, return the stored job and False."""

    @abstractmethod
    async def get(self, *, user_id: str, job_id: str) -> AiJob | None:
        """Return the user's job, or None if missing or owned by someone else."""

    @abstractmethod
    async def list_by_ids(self, *, user_id: str, job_ids: Sequence[str]) -> list[AiJob]:
        """Return the user's jobs among ``job_ids``; unknown ids are omitted."""

    @abstractmethod
    async def list_by_state(
        self,
        *,
        user_id: str,
        state: AiJobListState,
        since: datetime,
        limit: int,
    ) -> list[AiJob]:
        """Return the user's jobs created after ``since``, newest first."""

    @abstractmethod
    async def request_retry(self, *, user_id: str, job_id: str) -> AiJob:
        """Queue one more attempt of a failed, retryable job.

        Raises AiJobNotFoundError or AiJobNotRetryableError.
        """

    @abstractmethod
    async def request_cancel(self, *, user_id: str, job_id: str) -> AiJob:
        """Cancel a queued job now, or flag a running one for its worker.

        Finished jobs are returned unchanged. Raises AiJobNotFoundError.
        """

    @abstractmethod
    async def mark_seen(self, *, user_id: str, job_ids: Sequence[str]) -> int:
        """Mark the user's finished, unseen jobs as seen; return how many changed."""

    @abstractmethod
    async def find_active_chat_job_id(
        self, *, user_id: str, dedupe_key: str
    ) -> str | None:
        """Return the queued or running chat job for this turn, if any."""

    # --- worker ---

    @abstractmethod
    async def claim_next(
        self, *, lease_seconds: int, global_capacity: int
    ) -> AiJobClaim | None:
        """Lease the next due job, or None when idle or at capacity."""

    @abstractmethod
    async def heartbeat(
        self, claim: AiJobClaim, *, lease_seconds: int
    ) -> AiJobHeartbeat:
        """Extend the lease and report whether the user asked to cancel."""

    @abstractmethod
    async def complete(
        self,
        claim: AiJobClaim,
        *,
        result: dict[str, Any],
        notify_delay_seconds: int,
    ) -> bool:
        """Store the result; False if the claim no longer owns the job."""

    @abstractmethod
    async def fail_attempt(
        self,
        claim: AiJobClaim,
        *,
        error: AiJobError,
        retry_delay_seconds: float | None,
        notify_delay_seconds: int,
    ) -> AiJobStatus | None:
        """Record a failed attempt.

        With ``retry_delay_seconds`` the job goes back to the queue (keeping
        the error); without it the job fails. A pending cancel request turns
        either outcome into cancelled. Returns the new status, or None if the
        claim no longer owns the job.
        """

    @abstractmethod
    async def cancel_claimed(self, claim: AiJobClaim) -> bool:
        """Finish a cancelled attempt; False if the claim no longer owns the job."""

    @abstractmethod
    async def start_chat_job(
        self, job: NewAiJob, *, lease_seconds: int
    ) -> AiJobClaim | None:
        """Create (or take over) a chat job already running in this process.

        A queued or failed job with the same id and request is taken over.
        Returns None when the job is already running or finished.
        Raises AiJobConflictError when the id belongs to a different request.
        """

    # --- notifier ---

    @abstractmethod
    async def claim_notifications(
        self, *, limit: int, max_attempts: int, stale_after_seconds: int
    ) -> list[AiJobNotificationClaim]:
        """Reserve due, unseen, unannounced finished jobs for one push each.

        Reserved rows are marked as notified before anything is sent, so a
        crash can lose a push but never send it twice. Due rows older than
        ``stale_after_seconds`` are dropped from the queue instead.
        """

    @abstractmethod
    async def release_notification(
        self, *, job_id: str, retry_delay_seconds: float
    ) -> None:
        """Put a reserved notification back after a temporary send failure."""
