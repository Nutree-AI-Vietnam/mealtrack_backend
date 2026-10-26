"""One background AI job end to end on real PostgreSQL: queue, run, push."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from src.app.services.ai_jobs import (
    AiJobCancelSignals,
    AiJobNotifier,
    AiJobService,
    AiJobsSchemaProbe,
    AiJobWakeup,
    AiJobWorker,
    PushTokenService,
)
from src.app.services.ai_jobs.executors import AiJobInterrupt
from src.domain.exceptions.ai_job_exceptions import AiJobExecutionError
from src.domain.model.ai_jobs import (
    AiJobClaim,
    AiJobErrorKind,
    AiJobListState,
    AiJobStatus,
    AiJobType,
)
from src.domain.model.notification import (
    DeviceType,
    PushMessage,
    PushSendReport,
    PushTarget,
)
from src.domain.ports.push_sender_port import PushSenderPort
from src.infra.database.models.ai_job import AiJobORM
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork

pytestmark = pytest.mark.integration


class FlakyThenOkExecutor:
    """Fails like a provider outage on the first attempt, then succeeds."""

    def __init__(self) -> None:
        self.attempts: list[int] = []

    async def execute(
        self, claim: AiJobClaim, interrupt: AiJobInterrupt
    ) -> dict[str, Any]:
        self.attempts.append(claim.attempts)
        if claim.attempts == 1:
            raise AiJobExecutionError("AI_UNAVAILABLE", "provider down")
        return {"items": [{"name": "egg", "quantity": 2}]}


class OkExecutor:
    async def execute(
        self, claim: AiJobClaim, interrupt: AiJobInterrupt
    ) -> dict[str, Any]:
        return {"items": [{"name": claim.request_payload["text"], "quantity": 1}]}


class BlockingExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.interrupted = False

    async def execute(
        self, claim: AiJobClaim, interrupt: AiJobInterrupt
    ) -> dict[str, Any]:
        self.started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.interrupted = True
            raise
        return {}


class RecordingSender(PushSenderPort):
    def __init__(self) -> None:
        self.sent: list[tuple[list[PushTarget], PushMessage]] = []

    async def send(
        self, targets: Sequence[PushTarget], message: PushMessage
    ) -> PushSendReport:
        self.sent.append((list(targets), message))
        return PushSendReport(sent=len(targets))


@pytest.fixture
def jobs_db(monkeypatch, async_session_factory):
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    return async_session_factory


async def _user(session) -> str:
    user = User(
        id=str(uuid4()),
        firebase_uid=f"uid-{uuid4()}",
        email=f"pipeline-{uuid4()}@example.com",
        username=f"pipeline-{uuid4()}",
        password_hash="unused",
    )
    session.add(user)
    await session.commit()
    return user.id


def _service(probe: AiJobsSchemaProbe, signals: AiJobCancelSignals) -> AiJobService:
    return AiJobService(
        uow_factory=AsyncUnitOfWork,
        schema_probe=probe,
        wakeup=AiJobWakeup(),
        cancel_signals=signals,
        enabled=True,
        chat_background_enabled=True,
        lease_seconds=120,
    )


def _worker(
    executor: Any, signals: AiJobCancelSignals, *, notify_grace_seconds: int = 0
) -> AiJobWorker:
    return AiJobWorker(
        uow_factory=AsyncUnitOfWork,
        executors={AiJobType.PARSE_TEXT: executor},
        cancel_signals=signals,
        wakeup=AiJobWakeup(),
        concurrency=1,
        global_capacity=4,
        lease_seconds=120,
        heartbeat_seconds=0.5,
        deadline_seconds=30,
        poll_seconds=0.1,
        notify_grace_seconds=notify_grace_seconds,
        rng=lambda: 0.5,
    )


async def _enqueue(service: AiJobService, user_id: str, text: str = "2 eggs"):
    job, created = await service.enqueue(
        job_id=str(uuid4()),
        user_id=user_id,
        job_type=AiJobType.PARSE_TEXT,
        language="vi",
        request={"text": text, "current_items": None},
    )
    assert created
    return job


async def _make_due(factory, job_id: str, column: str) -> None:
    async with factory() as session:
        await session.execute(
            update(AiJobORM)
            .where(AiJobORM.id == job_id)
            .values({column: func.clock_timestamp()})
        )
        await session.commit()


async def _notify_after(factory, job_id: str):
    async with factory() as session:
        return (
            await session.execute(
                select(AiJobORM.notify_after).where(AiJobORM.id == job_id)
            )
        ).scalar_one()


@pytest.mark.asyncio
async def test_job_retries_completes_and_is_pushed_once(jobs_db, pg_session):
    user_id = await _user(pg_session)
    probe = AiJobsSchemaProbe(AsyncUnitOfWork)
    signals = AiJobCancelSignals()
    service = _service(probe, signals)
    tokens = PushTokenService(uow_factory=AsyncUnitOfWork, schema_probe=probe)
    assert await tokens.register(
        user_id=user_id, token="device-1", device_type=DeviceType.ANDROID
    )
    executor = FlakyThenOkExecutor()
    worker = _worker(executor, signals)
    sender = RecordingSender()
    notifier = AiJobNotifier(uow_factory=AsyncUnitOfWork, sender=sender)

    job = await _enqueue(service, user_id)
    assert job.status == AiJobStatus.QUEUED

    # The outage is transient, so the job waits for an automatic retry.
    assert await worker.run_once() is True
    job = await service.get(user_id=user_id, job_id=job.id)
    assert job.status == AiJobStatus.QUEUED
    assert job.attempts == 1
    assert job.error is not None
    assert (job.error.code, job.error.kind) == (
        "AI_UNAVAILABLE",
        AiJobErrorKind.TRANSIENT,
    )
    assert job.next_attempt_at is not None
    assert await worker.run_once() is False
    assert await notifier.run_once() == 0

    await _make_due(jobs_db, job.id, "available_at")
    assert await worker.run_once() is True
    job = await service.get(user_id=user_id, job_id=job.id)
    assert job.status == AiJobStatus.COMPLETED
    assert job.attempts == 2
    assert job.error is None
    assert job.result == {"items": [{"name": "egg", "quantity": 2}]}
    assert executor.attempts == [1, 2]

    open_jobs = await service.list_by_state(user_id=user_id, state=AiJobListState.OPEN)
    assert [open_job.id for open_job in open_jobs] == [job.id]

    assert await notifier.run_once() == 1
    assert await notifier.run_once() == 0
    [(targets, message)] = sender.sent
    assert targets == [PushTarget(token="device-1", device_type=DeviceType.ANDROID)]
    assert message.data == {
        "type": "ai_job",
        "job_id": job.id,
        "job_type": "parse_text",
        "status": "completed",
    }
    assert message.title == "Bữa ăn của bạn đã sẵn sàng"
    assert message.collapse_key == job.id

    assert await service.mark_seen(user_id=user_id, job_ids=[job.id]) == 1
    assert await service.list_by_state(user_id=user_id, state=AiJobListState.OPEN) == []


@pytest.mark.asyncio
async def test_result_seen_in_the_app_is_never_pushed(jobs_db, pg_session):
    user_id = await _user(pg_session)
    probe = AiJobsSchemaProbe(AsyncUnitOfWork)
    signals = AiJobCancelSignals()
    service = _service(probe, signals)
    tokens = PushTokenService(uow_factory=AsyncUnitOfWork, schema_probe=probe)
    await tokens.register(user_id=user_id, token="device-1", device_type=DeviceType.IOS)
    # A long grace period: the app shows the result before the push is due.
    worker = _worker(OkExecutor(), signals, notify_grace_seconds=600)
    sender = RecordingSender()
    notifier = AiJobNotifier(uow_factory=AsyncUnitOfWork, sender=sender)

    seen = await _enqueue(service, user_id, "2 eggs")
    away = await _enqueue(service, user_id, "1 banana")
    assert await worker.run_once() is True
    assert await worker.run_once() is True
    for job_id in (seen.id, away.id):
        job = await service.get(user_id=user_id, job_id=job_id)
        assert job.status == AiJobStatus.COMPLETED, job

    assert await service.mark_seen(user_id=user_id, job_ids=[seen.id]) == 1
    assert await _notify_after(jobs_db, seen.id) is None
    await _make_due(jobs_db, away.id, "notify_after")

    assert await notifier.run_once() == 1
    assert [message.data["job_id"] for _, message in sender.sent] == [away.id]


@pytest.mark.asyncio
async def test_cancel_stops_a_running_text_parse(jobs_db, pg_session):
    user_id = await _user(pg_session)
    probe = AiJobsSchemaProbe(AsyncUnitOfWork)
    signals = AiJobCancelSignals()
    service = _service(probe, signals)
    executor = BlockingExecutor()
    worker = _worker(executor, signals)

    job = await _enqueue(service, user_id)
    attempt = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(executor.started.wait(), timeout=5)

    requested = await service.cancel(user_id=user_id, job_id=job.id)
    assert requested.status == AiJobStatus.RUNNING
    assert await asyncio.wait_for(attempt, timeout=10) is True

    job = await service.get(user_id=user_id, job_id=job.id)
    assert job.status == AiJobStatus.CANCELLED
    assert job.error is None
    assert executor.interrupted
    assert await _notify_after(jobs_db, job.id) is None
