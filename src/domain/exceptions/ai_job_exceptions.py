"""Errors raised by background AI job use cases."""

from __future__ import annotations


class AiJobNotFoundError(Exception):
    """The job does not exist or belongs to another user."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__("AI job not found")


class AiJobConflictError(Exception):
    """The job id is already used for a different request."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__("AI job id was reused with a different request")


class AiJobNotRetryableError(Exception):
    """Only failed jobs whose error allows it can be retried by the user."""

    def __init__(self, job_id: str, status: str) -> None:
        self.job_id = job_id
        self.status = status
        super().__init__("AI job cannot be retried")


class AiJobsUnavailableError(Exception):
    """Background AI jobs are disabled or their schema is missing."""

    def __init__(self, reason: str = "disabled") -> None:
        self.reason = reason
        super().__init__("Background AI jobs are unavailable")


class AiJobExecutionError(Exception):
    """An executor failed with a known client-facing error code."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)
