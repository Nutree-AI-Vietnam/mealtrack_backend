"""Pushes finished AI jobs the user has not seen in the app.

A job becomes due a few seconds after it finishes. While the app is open it
marks results as seen when it shows them, which takes them out of the queue,
so only users who are away get a push.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from src.domain.model.ai_jobs import (
    AI_JOB_MAX_NOTIFY_ATTEMPTS,
    AI_JOB_NOTIFY_STALE_AFTER,
    NOTIFIABLE_AI_JOB_STATUSES,
    AiJobNotificationClaim,
    AiJobStatus,
    AiJobType,
)
from src.domain.model.ai_jobs.notification_copy import ai_job_push_copy
from src.domain.model.notification import PushMessage
from src.domain.ports.push_sender_port import PushSenderPort
from src.observability import increment_metric

logger = logging.getLogger(__name__)

AI_JOB_PUSH_THREAD_ID = "ai_job"
_RELEASE_BASE_DELAY_SECONDS = 30.0


def build_ai_job_push_message(claim: AiJobNotificationClaim) -> PushMessage | None:
    if claim.status not in NOTIFIABLE_AI_JOB_STATUSES:
        return None
    title, body = ai_job_push_copy(
        claim.job_type,
        claim.status,
        language=claim.language,
        retryable=claim.error_retryable is not False,
    )
    data = {
        "type": "ai_job",
        "job_id": claim.job_id,
        "job_type": claim.job_type.value,
        "status": claim.status.value,
    }
    if claim.status == AiJobStatus.COMPLETED:
        meal_id = (claim.result or {}).get("meal_id")
        if (
            claim.job_type == AiJobType.MEAL_SCAN
            and isinstance(meal_id, str)
            and meal_id
        ):
            data["meal_id"] = meal_id
    elif claim.error_code:
        data["error_code"] = claim.error_code
    return PushMessage(
        title=title,
        body=body,
        data=data,
        thread_id=AI_JOB_PUSH_THREAD_ID,
        collapse_key=claim.job_id,
    )


class AiJobNotifier:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], Any],
        sender: PushSenderPort,
        interval_seconds: float = 2.0,
        batch_size: int = 20,
        ready: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._sender = sender
        self._interval = interval_seconds
        self._batch_size = batch_size
        self._ready = ready

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            claimed = 0
            try:
                if self._ready is None or await self._ready():
                    claimed = await self.run_once()
            except Exception:
                logger.exception("AI job notifier pass failed")
            if claimed >= self._batch_size:
                # More may be due already.
                continue
            try:
                await asyncio.wait_for(stop.wait(), self._interval)
            except TimeoutError:
                pass

    async def run_once(self) -> int:
        """Deliver one batch of due notifications; return how many were claimed."""
        async with self._uow_factory() as uow:
            claims = await uow.ai_jobs.claim_notifications(
                limit=self._batch_size,
                max_attempts=AI_JOB_MAX_NOTIFY_ATTEMPTS,
                stale_after_seconds=int(AI_JOB_NOTIFY_STALE_AFTER.total_seconds()),
            )
        for claim in claims:
            try:
                await self.deliver(claim)
            except Exception:
                logger.exception(
                    "AI job push failed",
                    extra={"job_id": claim.job_id, "job_type": claim.job_type.value},
                )
        return len(claims)

    async def deliver(self, claim: AiJobNotificationClaim) -> None:
        message = build_ai_job_push_message(claim)
        if message is None:
            return
        # The claim is already committed. An error before the push goes out
        # has to put it back, or notified_at stays set and it is never retried.
        dispatched = False
        try:
            async with self._uow_factory() as uow:
                targets = await uow.push_tokens.list_active_targets(
                    user_id=claim.user_id
                )
            if not targets:
                _count(claim, "no_device")
                return
            report = await self._sender.send(targets, message)
            dispatched = report.sent > 0
            if report.invalid_tokens:
                async with self._uow_factory() as uow:
                    await uow.push_tokens.deactivate_tokens(report.invalid_tokens)
        except Exception:
            if not dispatched:
                await self._release_after_delivery_error(claim)
            raise
        if report.sent:
            _count(claim, "sent")
            return
        if report.retry_later and claim.notify_attempts < AI_JOB_MAX_NOTIFY_ATTEMPTS:
            await self._release_for_retry(claim)
            _count(claim, "retry_later")
            return
        _count(claim, "failed")

    async def _release_for_retry(self, claim: AiJobNotificationClaim) -> None:
        if claim.notify_attempts >= AI_JOB_MAX_NOTIFY_ATTEMPTS:
            return
        async with self._uow_factory() as uow:
            await uow.ai_jobs.release_notification(
                job_id=claim.job_id,
                retry_delay_seconds=_RELEASE_BASE_DELAY_SECONDS * claim.notify_attempts,
            )

    async def _release_after_delivery_error(
        self, claim: AiJobNotificationClaim
    ) -> None:
        try:
            await self._release_for_retry(claim)
        except Exception:
            logger.exception(
                "Failed to release AI job notification for retry",
                extra={"job_id": claim.job_id, "job_type": claim.job_type.value},
            )


def _count(claim: AiJobNotificationClaim, outcome: str) -> None:
    increment_metric(
        "ai_jobs.push",
        attributes={
            "job_type": claim.job_type.value,
            "status": claim.status.value,
            "outcome": outcome,
        },
    )
