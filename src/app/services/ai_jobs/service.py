"""Client-facing AI job use cases: enqueue, observe, retry, cancel, ack."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any
from uuid import uuid4

from src.app.services.ai_jobs.signals import (
    AiJobCancelSignals,
    AiJobsSchemaProbe,
    AiJobWakeup,
)
from src.domain.exceptions.ai_job_exceptions import (
    AiJobConflictError,
    AiJobNotFoundError,
    AiJobsUnavailableError,
)
from src.domain.model.ai_jobs import (
    AI_JOB_LIST_LIMIT,
    AI_JOB_MAX_ATTEMPTS,
    AI_JOB_OPEN_WINDOW,
    AI_JOB_POLL_INTERVAL_MS,
    ENQUEUEABLE_AI_JOB_TYPES,
    AiJob,
    AiJobClaim,
    AiJobListState,
    AiJobStatus,
    AiJobType,
    NewAiJob,
)
from src.domain.model.ai_jobs.policies import ai_job_request_fingerprint
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)


def new_chat_job(
    *,
    job_id: str,
    user_id: str,
    language: str,
    content: str,
    idempotency_key: str,
    locale: str | None,
    header_timezone: str | None,
    user_language: str | None,
    intent: str | None = None,
    function_name: str | None = None,
    function_args: dict[str, Any] | None = None,
    thread_id: str | None = None,
) -> NewAiJob:
    """Describe a chat turn so a worker can run it again exactly as sent."""
    request = {
        "content": content.strip(),
        "idempotency_key": idempotency_key,
        "intent": intent,
        "function_name": function_name,
        "function_args": function_args,
    }
    # Absent on the original single-thread route, which still means "the
    # user's most recently active thread".
    if thread_id is not None:
        request["thread_id"] = thread_id
    return NewAiJob(
        id=job_id,
        user_id=user_id,
        job_type=AiJobType.CHAT,
        language=language,
        # The resolved user language is stored so a retry resolves the
        # same chat locale, and so the same turn fingerprint.
        request_payload={
            **request,
            "locale": locale,
            "header_timezone": header_timezone,
            "user_language": user_language,
        },
        request_fingerprint=ai_job_request_fingerprint(AiJobType.CHAT, request),
        dedupe_key=idempotency_key,
    )


class AiJobService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], Any],
        schema_probe: AiJobsSchemaProbe,
        wakeup: AiJobWakeup,
        cancel_signals: AiJobCancelSignals,
        enabled: bool,
        chat_background_enabled: bool,
        lease_seconds: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._probe = schema_probe
        self._wakeup = wakeup
        self._cancel_signals = cancel_signals
        self._enabled = enabled
        self._chat_background_enabled = chat_background_enabled
        self._lease_seconds = lease_seconds

    async def capabilities(self) -> dict[str, Any]:
        """Raise AiJobsUnavailableError when the schema is not migrated."""
        await self._require_schema()
        return {
            "ai_jobs": self._enabled,
            "types": [job_type.value for job_type in AiJobType],
            "max_attempts": AI_JOB_MAX_ATTEMPTS,
            "poll_interval_ms": AI_JOB_POLL_INTERVAL_MS,
            "chat_background": self._enabled and self._chat_background_enabled,
        }

    async def enqueue(
        self,
        *,
        job_id: str,
        user_id: str,
        job_type: AiJobType,
        language: str,
        request: dict[str, Any],
    ) -> tuple[AiJob, bool]:
        """Queue a job; return it and whether this call created it.

        Re-sending the same id and request returns the existing job. Raises
        AiJobConflictError when the id belongs to another request or user.
        """
        if job_type not in ENQUEUEABLE_AI_JOB_TYPES:
            raise ValueError(f"{job_type} jobs are not enqueued directly")
        await self._require_writable()
        fingerprint = ai_job_request_fingerprint(job_type, request)
        payload = dict(request)
        if job_type == AiJobType.MEAL_SCAN:
            # Every attempt saves the same meal row.
            payload["meal_id"] = str(uuid4())
        async with self._uow_factory() as uow:
            job, created = await uow.ai_jobs.create(
                NewAiJob(
                    id=job_id,
                    user_id=user_id,
                    job_type=job_type,
                    language=language,
                    request_payload=payload,
                    request_fingerprint=fingerprint,
                )
            )
        if not created and (
            job.user_id != user_id
            or job.job_type != job_type
            or job.request_fingerprint != fingerprint
        ):
            raise AiJobConflictError(job_id)
        if created:
            self._wakeup.notify()
        return job, created

    async def get(self, *, user_id: str, job_id: str) -> AiJob:
        await self._require_schema()
        async with self._uow_factory() as uow:
            job = await uow.ai_jobs.get(user_id=user_id, job_id=job_id)
        if job is None:
            raise AiJobNotFoundError(job_id)
        return job

    async def list_by_ids(self, *, user_id: str, job_ids: Sequence[str]) -> list[AiJob]:
        await self._require_schema()
        unique = list(dict.fromkeys(job_ids))
        if not unique:
            return []
        async with self._uow_factory() as uow:
            return await uow.ai_jobs.list_by_ids(user_id=user_id, job_ids=unique)

    async def list_by_state(
        self, *, user_id: str, state: AiJobListState
    ) -> list[AiJob]:
        await self._require_schema()
        async with self._uow_factory() as uow:
            return await uow.ai_jobs.list_by_state(
                user_id=user_id,
                state=state,
                since=utc_now() - AI_JOB_OPEN_WINDOW,
                limit=AI_JOB_LIST_LIMIT,
            )

    async def retry(self, *, user_id: str, job_id: str) -> AiJob:
        await self._require_writable()
        async with self._uow_factory() as uow:
            job = await uow.ai_jobs.request_retry(user_id=user_id, job_id=job_id)
        self._wakeup.notify()
        return job

    async def cancel(self, *, user_id: str, job_id: str) -> AiJob:
        await self._require_schema()
        async with self._uow_factory() as uow:
            job = await uow.ai_jobs.request_cancel(user_id=user_id, job_id=job_id)
        if job.status == AiJobStatus.RUNNING:
            self._cancel_signals.signal(job.id)
        return job

    async def mark_seen(self, *, user_id: str, job_ids: Sequence[str]) -> int:
        await self._require_schema()
        unique = list(dict.fromkeys(job_ids))
        if not unique:
            return 0
        async with self._uow_factory() as uow:
            return await uow.ai_jobs.mark_seen(user_id=user_id, job_ids=unique)

    async def start_chat_job(self, job: NewAiJob) -> AiJobClaim | None:
        """Register a chat turn this process is about to stream.

        Returns None when the job is already running or finished elsewhere.
        Raises AiJobsUnavailableError when background chat is off and
        AiJobConflictError when the id belongs to another request.
        """
        if not self._chat_background_enabled:
            raise AiJobsUnavailableError("chat_disabled")
        await self._require_writable()
        async with self._uow_factory() as uow:
            return await uow.ai_jobs.start_chat_job(
                job, lease_seconds=self._lease_seconds
            )

    async def find_active_chat_job_id(
        self, *, user_id: str, dedupe_key: str
    ) -> str | None:
        """Best effort: the chat thread still loads if this lookup fails."""
        if not self._enabled:
            return None
        try:
            if not await self._probe.ready():
                return None
            async with self._uow_factory() as uow:
                return await uow.ai_jobs.find_active_chat_job_id(
                    user_id=user_id, dedupe_key=dedupe_key
                )
        except Exception:
            logger.warning("AI chat job lookup failed", exc_info=True)
            return None

    async def _require_schema(self) -> None:
        if not await self._probe.ready():
            raise AiJobsUnavailableError("schema_missing")

    async def _require_writable(self) -> None:
        if not self._enabled:
            raise AiJobsUnavailableError("disabled")
        await self._require_schema()
