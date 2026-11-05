"""Enumerations for background AI jobs."""

from __future__ import annotations

from enum import StrEnum


class AiJobType(StrEnum):
    CHAT = "chat"
    MEAL_SCAN = "meal_scan"
    PARSE_TEXT = "parse_text"
    RECIPE_IMPORT = "recipe_import"
    WEEKLY_PLAN = "weekly_plan"
    # Ghi nhanh: one sentence the app hands off, logged like its sync route.
    DESCRIBE_LOG = "describe_log"  # POST /v1/log/describe (+ panel, supplements)
    DESCRIBE_MOVEMENT = "describe_movement"  # POST /v1/movement/describe
    DESCRIBE_HYDRATION = "describe_hydration"  # POST /v1/hydration/describe


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
# A week is asked for through the meal-plan endpoints, which know the week
# and the user's targets, never through POST /v1/ai-jobs.
DESCRIBE_AI_JOB_TYPES = frozenset(
    {
        AiJobType.DESCRIBE_LOG,
        AiJobType.DESCRIBE_MOVEMENT,
        AiJobType.DESCRIBE_HYDRATION,
    }
)
ENQUEUEABLE_AI_JOB_TYPES = frozenset(
    {AiJobType.MEAL_SCAN, AiJobType.PARSE_TEXT, AiJobType.RECIPE_IMPORT}
    | DESCRIBE_AI_JOB_TYPES
)
# Jobs that run for minutes rather than seconds. Workers give them their own
# lanes and their own capacity, so a burst of weeks never queues a meal scan
# that someone is watching a spinner for.
LONG_AI_JOB_TYPES = frozenset({AiJobType.WEEKLY_PLAN})
