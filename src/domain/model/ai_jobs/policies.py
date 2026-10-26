"""Retry, error-classification, and request-identity rules for AI jobs."""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable, Mapping
from typing import Any

from src.domain.model.ai_jobs.constants import (
    AI_JOB_BASE_BACKOFF_SECONDS,
    AI_JOB_MAX_BACKOFF_SECONDS,
)
from src.domain.model.ai_jobs.enums import AiJobErrorKind, AiJobType
from src.domain.model.ai_jobs.models import AiJobError

# The user did something the AI cannot work with; trying again cannot help.
PERMANENT_NOT_RETRYABLE_CODES = frozenset(
    {
        "NOT_FOOD_IMAGE",
        "NOT_FOOD_LABEL_IMAGE",
        "AI_CONTENT_REJECTED",
        "CHAT_DAILY_LIMIT",
        "CHAT_STOPPED",
        "INVALID_JOB_INPUT",
        "NUTRITION_UNIT_INVALID",
    }
)
# The AI gave a bad answer; another manual attempt may succeed, an automatic
# one is unlikely to.
PERMANENT_RETRYABLE_CODES = frozenset(
    {
        "AI_OUTPUT_INVALID",
        "NUTRITION_INTEGRITY_REJECTED",
        "NOT_FOOD_TEXT",
    }
)
# Infrastructure trouble; retried automatically. Unknown codes are treated
# the same way.
TRANSIENT_CODES = frozenset(
    {
        "AI_UNAVAILABLE",
        "AI_TIMEOUT",
        "NUTRITION_PROVIDER_UNAVAILABLE",
        "CHAT_PROVIDER_UNAVAILABLE",
        "CHAT_BUSY",
        "CHAT_RATE_LIMITED",
        "CHAT_TURN_FAILED",
        "WORKER_INTERRUPTED",
        "INTERNAL_ERROR",
        "FAILED_TO_ANALYZE_MEAL_IMAGE",
    }
)


def classify_ai_job_error(code: str) -> AiJobError:
    if code in PERMANENT_NOT_RETRYABLE_CODES:
        return AiJobError(code=code, kind=AiJobErrorKind.PERMANENT, retryable=False)
    if code in PERMANENT_RETRYABLE_CODES:
        return AiJobError(code=code, kind=AiJobErrorKind.PERMANENT, retryable=True)
    return AiJobError(code=code, kind=AiJobErrorKind.TRANSIENT, retryable=True)


def should_auto_retry(error: AiJobError, *, attempts: int, max_attempts: int) -> bool:
    """Only transient failures with attempts left are retried automatically."""
    return error.kind == AiJobErrorKind.TRANSIENT and attempts < max_attempts


def retry_backoff_seconds(
    attempt: int, *, rng: Callable[[], float] = random.random
) -> float:
    """Delay before the retry that follows ``attempt`` (1-based), with ±20% jitter."""
    base = min(
        AI_JOB_MAX_BACKOFF_SECONDS,
        AI_JOB_BASE_BACKOFF_SECONDS * 2 ** max(0, attempt - 1),
    )
    return base * (0.8 + 0.4 * rng())


def can_retry_manually(error: AiJobError | None) -> bool:
    return error is None or error.retryable


def ai_job_request_fingerprint(job_type: AiJobType, payload: Mapping[str, Any]) -> str:
    """Stable hash of what the client asked for, to detect id reuse."""
    canonical = json.dumps(
        {"type": str(job_type), "input": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
