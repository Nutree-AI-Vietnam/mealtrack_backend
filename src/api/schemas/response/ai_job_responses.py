"""Background AI job response DTOs."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.domain.model.ai_jobs import AiJob


class AiJobErrorResponse(BaseModel):
    code: str
    kind: str = Field(..., description="transient or permanent")
    retryable: bool = Field(..., description="Whether the user can retry the job")


class AiJobResponse(BaseModel):
    id: str
    type: str
    status: str = Field(
        ..., description="queued, running, completed, failed or cancelled"
    )
    attempts: int
    max_attempts: int
    next_attempt_at: datetime | None = Field(
        None, description="When a queued automatic retry runs"
    )
    error: AiJobErrorResponse | None = None
    result: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    seen_at: datetime | None = None

    @classmethod
    def from_domain(cls, job: AiJob) -> "AiJobResponse":
        error = (
            AiJobErrorResponse(
                code=job.error.code,
                kind=job.error.kind.value,
                retryable=job.error.retryable,
            )
            if job.error is not None
            else None
        )
        return cls(
            id=job.id,
            type=job.job_type.value,
            status=job.status.value,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            next_attempt_at=job.next_attempt_at,
            error=error,
            result=job.result,
            created_at=job.created_at,
            updated_at=job.updated_at,
            completed_at=job.completed_at,
            seen_at=job.seen_at,
        )


class AiJobListResponse(BaseModel):
    jobs: list[AiJobResponse]


class AiJobSeenResponse(BaseModel):
    updated: int
