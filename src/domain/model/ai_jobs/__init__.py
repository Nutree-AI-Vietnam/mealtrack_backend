"""Background AI job domain models."""

from src.domain.model.ai_jobs.constants import (
    AI_JOB_ENQUEUE_RATE_LIMIT,
    AI_JOB_LIST_LIMIT,
    AI_JOB_MAX_ATTEMPTS,
    AI_JOB_MAX_IDS_PER_LOOKUP,
    AI_JOB_MAX_NOTIFY_ATTEMPTS,
    AI_JOB_MAX_SEEN_IDS,
    AI_JOB_NOTIFY_STALE_AFTER,
    AI_JOB_OPEN_WINDOW,
    AI_JOB_POLL_INTERVAL_MS,
)
from src.domain.model.ai_jobs.enums import (
    ACTIVE_AI_JOB_STATUSES,
    ENQUEUEABLE_AI_JOB_TYPES,
    NOTIFIABLE_AI_JOB_STATUSES,
    TERMINAL_AI_JOB_STATUSES,
    AiJobErrorKind,
    AiJobListState,
    AiJobStatus,
    AiJobType,
)
from src.domain.model.ai_jobs.models import (
    AiJob,
    AiJobClaim,
    AiJobError,
    AiJobHeartbeat,
    AiJobNotificationClaim,
    NewAiJob,
)

__all__ = [
    "ACTIVE_AI_JOB_STATUSES",
    "AI_JOB_ENQUEUE_RATE_LIMIT",
    "AI_JOB_LIST_LIMIT",
    "AI_JOB_MAX_ATTEMPTS",
    "AI_JOB_MAX_IDS_PER_LOOKUP",
    "AI_JOB_MAX_NOTIFY_ATTEMPTS",
    "AI_JOB_MAX_SEEN_IDS",
    "AI_JOB_NOTIFY_STALE_AFTER",
    "AI_JOB_OPEN_WINDOW",
    "AI_JOB_POLL_INTERVAL_MS",
    "ENQUEUEABLE_AI_JOB_TYPES",
    "NOTIFIABLE_AI_JOB_STATUSES",
    "TERMINAL_AI_JOB_STATUSES",
    "AiJob",
    "AiJobClaim",
    "AiJobError",
    "AiJobErrorKind",
    "AiJobHeartbeat",
    "AiJobListState",
    "AiJobNotificationClaim",
    "AiJobStatus",
    "AiJobType",
    "NewAiJob",
]
