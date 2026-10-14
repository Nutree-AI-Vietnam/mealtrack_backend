"""Fakes and builders for background AI job tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, create_autospec

from src.domain.model.ai_jobs import (
    AiJob,
    AiJobClaim,
    AiJobError,
    AiJobErrorKind,
    AiJobNotificationClaim,
    AiJobStatus,
    AiJobType,
)
from src.domain.ports.ai_job_repository_port import AiJobRepositoryPort
from src.domain.ports.push_token_repository_port import PushTokenRepositoryPort

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
JOB_ID = "6f1c2f4e-3b7a-4c55-9a51-0d9a7f3e2b10"
USER_ID = "u1"


class FakeAiJobsUow:
    """Unit of work whose repositories are autospecced mocks.

    Calling an instance returns the instance, so it also serves as the
    ``uow_factory`` that services take.
    """

    def __init__(self) -> None:
        self.ai_jobs = create_autospec(AiJobRepositoryPort, instance=True)
        self.push_tokens = create_autospec(PushTokenRepositoryPort, instance=True)
        self.meals = AsyncMock()
        self.opened = 0
        self.committed = 0
        self.rolled_back = 0

    def __call__(self) -> FakeAiJobsUow:
        return self

    async def __aenter__(self) -> FakeAiJobsUow:
        self.opened += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.committed += 1
        else:
            self.rolled_back += 1


class StubSchemaProbe:
    """Stands in for ``AiJobsSchemaProbe`` with a fixed answer."""

    def __init__(self, ready: bool = True) -> None:
        self.is_ready = ready
        self.calls = 0

    async def ready(self) -> bool:
        self.calls += 1
        return self.is_ready


def make_error(
    code: str = "AI_UNAVAILABLE",
    *,
    kind: AiJobErrorKind = AiJobErrorKind.TRANSIENT,
    retryable: bool = True,
) -> AiJobError:
    return AiJobError(code=code, kind=kind, retryable=retryable)


def make_job(**overrides: Any) -> AiJob:
    job = AiJob(
        id=JOB_ID,
        user_id=USER_ID,
        job_type=AiJobType.PARSE_TEXT,
        status=AiJobStatus.QUEUED,
        attempts=0,
        max_attempts=4,
        language="en",
        request_payload={"text": "2 eggs", "current_items": None},
        request_fingerprint="fingerprint",
        available_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    return replace(job, **overrides)


def make_claim(**overrides: Any) -> AiJobClaim:
    claim = AiJobClaim(
        id=JOB_ID,
        user_id=USER_ID,
        job_type=AiJobType.PARSE_TEXT,
        language="en",
        request_payload={"text": "2 eggs", "current_items": None},
        claim_token="claim-token",
        attempts=1,
        max_attempts=4,
        lease_expires_at=NOW + timedelta(seconds=120),
    )
    return replace(claim, **overrides)


def make_notification_claim(**overrides: Any) -> AiJobNotificationClaim:
    claim = AiJobNotificationClaim(
        job_id=JOB_ID,
        user_id=USER_ID,
        job_type=AiJobType.MEAL_SCAN,
        status=AiJobStatus.COMPLETED,
        language="en",
        result={"meal_id": "meal-1"},
        error_code=None,
        error_retryable=None,
        notify_attempts=1,
    )
    return replace(claim, **overrides)
