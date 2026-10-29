"""Sends due server-side engagement pushes from the ``notifications`` outbox.

Rows are claimed (moved to ``processing``) and committed before anything is
sent, so a crash can lose a push but never sends it twice. Rows nobody could
send before ``expires_at`` are expired on each pass.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Any

from src.domain.model.notification import (
    ENGAGEMENT_PUSH_ROUTES,
    EngagementPushClaim,
    PushMessage,
    engagement_push_copy,
)
from src.domain.ports.push_sender_port import PushSenderPort
from src.observability import increment_metric

logger = logging.getLogger(__name__)

ENGAGEMENT_PUSH_TYPE = "engagement"


def build_engagement_push_message(claim: EngagementPushClaim) -> PushMessage:
    language = claim.context.get("language")
    title, body = engagement_push_copy(
        claim.kind, language=language if isinstance(language, str) else None
    )
    return PushMessage(
        title=title,
        body=body,
        # FCM data values must be strings. The app renders Android data-only
        # pushes from data["title"] and data["body"], which the sender adds.
        data={
            "type": ENGAGEMENT_PUSH_TYPE,
            "kind": claim.kind.value,
            "route": ENGAGEMENT_PUSH_ROUTES[claim.kind],
        },
        collapse_key=claim.kind.value,
    )


class EngagementPushDispatcher:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], Any],
        sender: PushSenderPort,
        interval_seconds: float = 30.0,
        batch_size: int = 20,
    ) -> None:
        self._uow_factory = uow_factory
        self._sender = sender
        self._interval = interval_seconds
        self._batch_size = batch_size

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            claimed = 0
            try:
                claimed = await self.run_once()
            except Exception:
                logger.exception("Engagement push dispatcher pass failed")
            if claimed >= self._batch_size:
                # More may be due already.
                continue
            try:
                await asyncio.wait_for(stop.wait(), self._interval)
            except TimeoutError:
                pass

    async def run_once(self) -> int:
        """Expire stale pushes and send one batch of due ones.

        Returns how many pushes were claimed.
        """
        async with self._uow_factory() as uow:
            expired = await uow.engagement_pushes.expire_stale()
            claims = await uow.engagement_pushes.claim_due(limit=self._batch_size)
        if expired:
            increment_metric("engagement_push.expired", float(expired))
        for claim in claims:
            try:
                await self.deliver(claim)
            except Exception:
                logger.exception(
                    "Engagement push failed",
                    extra={"push_id": claim.id, "kind": claim.kind.value},
                )
        return len(claims)

    async def deliver(self, claim: EngagementPushClaim) -> None:
        try:
            message = build_engagement_push_message(claim)
            async with self._uow_factory() as uow:
                targets = await uow.push_tokens.list_active_targets(
                    user_id=claim.user_id
                )
            report = await self._sender.send(targets, message) if targets else None
        except Exception:
            # Nothing went out; settle the row so it does not stay processing.
            await self._settle_after_error(claim)
            raise
        if report is None:
            await self._settle(claim, sent=False, outcome="no_device")
            return
        if report.invalid_tokens:
            await self._deactivate(claim, report.invalid_tokens)
        if report.sent:
            await self._settle(claim, sent=True, outcome="sent")
        else:
            await self._settle(claim, sent=False, outcome="failed")

    async def _settle(
        self, claim: EngagementPushClaim, *, sent: bool, outcome: str
    ) -> None:
        async with self._uow_factory() as uow:
            if sent:
                await uow.engagement_pushes.mark_sent(claim.id)
            else:
                await uow.engagement_pushes.mark_failed(claim.id)
        _count(claim, outcome)

    async def _settle_after_error(self, claim: EngagementPushClaim) -> None:
        try:
            await self._settle(claim, sent=False, outcome="error")
        except Exception:
            logger.exception(
                "Failed to mark engagement push as failed",
                extra={"push_id": claim.id, "kind": claim.kind.value},
            )

    async def _deactivate(
        self, claim: EngagementPushClaim, tokens: Sequence[str]
    ) -> None:
        # The push already went out (or never can); a failure here must not
        # change how the row is settled.
        try:
            async with self._uow_factory() as uow:
                await uow.push_tokens.deactivate_tokens(tokens)
        except Exception:
            logger.exception(
                "Failed to deactivate rejected push tokens",
                extra={"push_id": claim.id, "kind": claim.kind.value},
            )


def _count(claim: EngagementPushClaim, outcome: str) -> None:
    increment_metric(
        "engagement_push.dispatch",
        attributes={"kind": claim.kind.value, "outcome": outcome},
    )
