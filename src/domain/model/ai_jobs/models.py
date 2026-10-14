"""Domain models for background AI jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.domain.model.ai_jobs.constants import AI_JOB_MAX_ATTEMPTS
from src.domain.model.ai_jobs.enums import (
    TERMINAL_AI_JOB_STATUSES,
    AiJobErrorKind,
    AiJobStatus,
    AiJobType,
)


@dataclass(frozen=True, slots=True)
class AiJobError:
    """Last failure of a job, in terms the client can act on."""

    code: str
    kind: AiJobErrorKind
    # Whether the fail UI offers "Try again".
    retryable: bool


@dataclass(frozen=True, slots=True)
class AiJob:
    id: str
    user_id: str
    job_type: AiJobType
    status: AiJobStatus
    attempts: int
    max_attempts: int
    language: str
    request_payload: dict[str, Any]
    request_fingerprint: str
    available_at: datetime
    created_at: datetime
    updated_at: datetime
    error: AiJobError | None = None
    result: dict[str, Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    seen_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    notified_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_AI_JOB_STATUSES

    @property
    def next_attempt_at(self) -> datetime | None:
        """When the pending automatic retry runs, if one is pending."""
        if self.status == AiJobStatus.QUEUED and self.error is not None:
            return self.available_at
        return None


@dataclass(frozen=True, slots=True)
class NewAiJob:
    id: str
    user_id: str
    job_type: AiJobType
    language: str
    request_payload: dict[str, Any]
    request_fingerprint: str
    # Links a chat job to its turn (the chat Idempotency-Key).
    dedupe_key: str | None = None
    max_attempts: int = AI_JOB_MAX_ATTEMPTS


@dataclass(frozen=True, slots=True)
class AiJobClaim:
    """A worker's leased right to run one attempt of a job."""

    id: str
    user_id: str
    job_type: AiJobType
    language: str
    request_payload: dict[str, Any]
    claim_token: str
    attempts: int
    max_attempts: int
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class AiJobHeartbeat:
    renewed: bool
    cancel_requested: bool


@dataclass(frozen=True, slots=True)
class AiJobNotificationClaim:
    """A finished job the notifier has taken responsibility for announcing."""

    job_id: str
    user_id: str
    job_type: AiJobType
    status: AiJobStatus
    language: str
    result: dict[str, Any] | None
    error_code: str | None
    error_retryable: bool | None
    notify_attempts: int
