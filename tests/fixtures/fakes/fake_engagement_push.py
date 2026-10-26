"""Fakes for the server-sent engagement push outbox and its dispatcher."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import create_autospec
from uuid import uuid4

from src.domain.model.notification import (
    EngagementPushClaim,
    EngagementPushKind,
    EngagementPushStatus,
    NewEngagementPush,
    PushMessage,
    PushSendReport,
    PushTarget,
)
from src.domain.ports.engagement_push_repository_port import (
    EngagementPushRepositoryPort,
)
from src.domain.ports.push_sender_port import PushSenderPort
from src.domain.ports.push_token_repository_port import PushTokenRepositoryPort

NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
USER_ID = "user-1"

PENDING = EngagementPushStatus.PENDING.value
PROCESSING = EngagementPushStatus.PROCESSING.value
SENT = EngagementPushStatus.SENT.value
FAILED = EngagementPushStatus.FAILED.value
EXPIRED = EngagementPushStatus.EXPIRED.value


@dataclass
class OutboxRow:
    id: str
    user_id: str
    kind: EngagementPushKind
    scheduled_date: date
    scheduled_for_utc: datetime
    expires_at: datetime
    context: dict[str, Any]
    status: str = PENDING


class FakeEngagementPushRepository(EngagementPushRepositoryPort):
    """In-memory outbox with the repository's status rules and a set clock."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now
        self.rows: dict[str, OutboxRow] = {}
        self.claim_limits: list[int] = []

    def add(self, *, status: str = PENDING, **overrides: Any) -> OutboxRow:
        values: dict[str, Any] = {
            "id": str(uuid4()),
            "user_id": USER_ID,
            "kind": EngagementPushKind.BILLING_ISSUE,
            "scheduled_for_utc": self.now - timedelta(minutes=1),
            "context": {"kind": "billing_issue", "language": "en"},
            "status": status,
        }
        values.update(overrides)
        values.setdefault("scheduled_date", values["scheduled_for_utc"].date())
        values.setdefault(
            "expires_at", values["scheduled_for_utc"] + timedelta(hours=48)
        )
        row = OutboxRow(**values)
        self.rows[row.id] = row
        return row

    def status(self, push_id: str) -> str:
        return self.rows[push_id].status

    async def enqueue(self, push: NewEngagementPush) -> bool:
        key = (push.user_id, push.kind, push.scheduled_date)
        if any(
            (r.user_id, r.kind, r.scheduled_date) == key for r in self.rows.values()
        ):
            return False
        self.rows[push.id] = OutboxRow(
            id=push.id,
            user_id=push.user_id,
            kind=push.kind,
            scheduled_date=push.scheduled_date,
            scheduled_for_utc=push.scheduled_for_utc,
            expires_at=push.expires_at,
            context=dict(push.context),
        )
        return True

    async def claim_due(self, *, limit: int) -> list[EngagementPushClaim]:
        self.claim_limits.append(limit)
        due = sorted(
            (
                row
                for row in self.rows.values()
                if row.status == PENDING
                and row.scheduled_for_utc <= self.now
                and row.expires_at > self.now
            ),
            key=lambda row: (row.scheduled_for_utc, row.id),
        )[:limit]
        for row in due:
            row.status = PROCESSING
        return [
            EngagementPushClaim(
                id=row.id, user_id=row.user_id, kind=row.kind, context=dict(row.context)
            )
            for row in due
        ]

    async def mark_sent(self, push_id: str) -> bool:
        return self._finish(push_id, SENT)

    async def mark_failed(self, push_id: str) -> bool:
        return self._finish(push_id, FAILED)

    async def expire_stale(self) -> int:
        stale = [
            row
            for row in self.rows.values()
            if row.status in (PENDING, PROCESSING) and row.expires_at <= self.now
        ]
        for row in stale:
            row.status = EXPIRED
        return len(stale)

    async def delete_pending(self, *, user_id: str, kind: EngagementPushKind) -> int:
        doomed = [
            push_id
            for push_id, row in self.rows.items()
            if row.user_id == user_id and row.kind == kind and row.status == PENDING
        ]
        for push_id in doomed:
            del self.rows[push_id]
        return len(doomed)

    def _finish(self, push_id: str, status: str) -> bool:
        row = self.rows.get(push_id)
        if row is None or row.status != PROCESSING:
            return False
        row.status = status
        return True


class FakeEngagementPushUow:
    """Unit of work over the fake outbox; calling it returns itself, so it
    also serves as the ``uow_factory`` the dispatcher takes."""

    def __init__(self, outbox: FakeEngagementPushRepository | None = None) -> None:
        self.engagement_pushes = outbox or FakeEngagementPushRepository()
        self.push_tokens = create_autospec(PushTokenRepositoryPort, instance=True)
        self.push_tokens.list_active_targets.return_value = []
        self.push_tokens.deactivate_tokens.return_value = 0
        self.opened = 0
        self.committed = 0
        self.rolled_back = 0

    def __call__(self) -> FakeEngagementPushUow:
        return self

    async def __aenter__(self) -> FakeEngagementPushUow:
        self.opened += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.committed += 1
        else:
            self.rolled_back += 1


@dataclass
class FakePushSender(PushSenderPort):
    """Records every send; answers with ``report`` or raises ``error``."""

    report: PushSendReport = field(default_factory=lambda: PushSendReport(sent=1))
    error: Exception | None = None
    calls: list[tuple[list[PushTarget], PushMessage]] = field(default_factory=list)

    async def send(
        self, targets: Sequence[PushTarget], message: PushMessage
    ) -> PushSendReport:
        self.calls.append((list(targets), message))
        if self.error is not None:
            raise self.error
        return self.report
