"""Enumerations for background AI jobs."""

from __future__ import annotations

from enum import StrEnum


class AiJobType(StrEnum):
    CHAT = "chat"
    MEAL_SCAN = "meal_scan"
    PARSE_TEXT = "parse_text"


class AiJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AiJobErrorKind(StrEnum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class AiJobListState(StrEnum):
    """Filters for listing a user's jobs."""

    # Active jobs plus finished jobs the user has not seen yet.
    OPEN = "open"
    ACTIVE = "active"
    UNSEEN = "unseen"


ACTIVE_AI_JOB_STATUSES = frozenset({AiJobStatus.QUEUED, AiJobStatus.RUNNING})
TERMINAL_AI_JOB_STATUSES = frozenset(
    {AiJobStatus.COMPLETED, AiJobStatus.FAILED, AiJobStatus.CANCELLED}
)
# Cancelled jobs are never announced.
NOTIFIABLE_AI_JOB_STATUSES = frozenset({AiJobStatus.COMPLETED, AiJobStatus.FAILED})
# Chat turns start through the chat endpoint, never through POST /v1/ai-jobs.
ENQUEUEABLE_AI_JOB_TYPES = frozenset({AiJobType.MEAL_SCAN, AiJobType.PARSE_TEXT})
