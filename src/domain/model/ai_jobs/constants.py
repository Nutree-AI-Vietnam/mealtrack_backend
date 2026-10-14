"""Limits shared by the AI job API, worker, and notifier."""

from __future__ import annotations

from datetime import timedelta

# One first run plus three automatic retries.
AI_JOB_MAX_ATTEMPTS = 4
AI_JOB_BASE_BACKOFF_SECONDS = 5
AI_JOB_MAX_BACKOFF_SECONDS = 3600

AI_JOB_MAX_IDS_PER_LOOKUP = 20
AI_JOB_MAX_SEEN_IDS = 50
AI_JOB_LIST_LIMIT = 20
AI_JOB_OPEN_WINDOW = timedelta(hours=24)
AI_JOB_POLL_INTERVAL_MS = 2500
AI_JOB_ENQUEUE_RATE_LIMIT = "30/minute"

AI_JOB_MAX_NOTIFY_ATTEMPTS = 3
# A result nobody was told about within this window is no longer news.
AI_JOB_NOTIFY_STALE_AFTER = timedelta(hours=1)
