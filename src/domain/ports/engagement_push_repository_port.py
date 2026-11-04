"""Persistence port for the server-sent engagement push outbox."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.model.notification import (
    EngagementPushClaim,
    EngagementPushKind,
    NewEngagementPush,
)


class EngagementPushRepositoryPort(ABC):
    """Rows of the ``notifications`` table whose kind is an engagement push.

    Times are taken from the database clock. Callers own the transaction.
    """

    @abstractmethod
    async def enqueue(self, push: NewEngagementPush) -> bool:
        """Queue a pending push; False if the user already has one of this
        kind for that local date."""

    @abstractmethod
    async def claim_due(self, *, limit: int) -> list[EngagementPushClaim]:
        """Move up to ``limit`` due, unexpired pending pushes to processing.

        Rows another dispatcher holds are skipped, never waited for.
        """

    @abstractmethod
    async def mark_sent(self, push_id: str) -> bool:
        """Record a delivered push; False if it was no longer processing."""

    @abstractmethod
    async def mark_failed(self, push_id: str) -> bool:
        """Record a push that reached no device; False if it was no longer
        processing."""

    @abstractmethod
    async def expire_stale(self) -> int:
        """Mark pending or processing pushes past ``expires_at`` as expired."""

    @abstractmethod
    async def delete_pending(self, *, user_id: str, kind: EngagementPushKind) -> int:
        """Drop the user's pending pushes of this kind, e.g. once the issue
        they announce is resolved."""
