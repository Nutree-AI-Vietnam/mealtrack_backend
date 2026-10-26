"""Background AI job queue semantics on real PostgreSQL (clock_timestamp, SKIP LOCKED)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update

from src.domain.exceptions.ai_job_exceptions import (
    AiJobConflictError,
    AiJobNotFoundError,
    AiJobNotRetryableError,
)
from src.domain.model.ai_jobs import (
    AiJobError,
    AiJobErrorKind,
    AiJobListState,
    AiJobStatus,
    AiJobType,
    NewAiJob,
)
from src.domain.model.notification import DeviceType
from src.infra.database.models.ai_job import AiJobORM
from src.infra.database.models.notification.user_fcm_token import UserFcmTokenORM
from src.infra.database.models.user.user import User
from src.infra.repositories.ai_job_repository import AsyncAiJobRepository
from src.infra.repositories.push_token_repository import AsyncPushTokenRepository

pytestmark = pytest.mark.integration

TRANSIENT = AiJobError("AI_UNAVAILABLE", AiJobErrorKind.TRANSIENT, True)
PERMANENT = AiJobError("NOT_FOOD_IMAGE", AiJobErrorKind.PERMANENT, False)


async def _user(session, name: str = "alice") -> str:
    user = User(
        id=str(uuid4()),
        firebase_uid=f"uid-{name}-{uuid4()}",
        email=f"{name}-{uuid4()}@example.com",
        username=f"{name}-{uuid4()}",
        password_hash="unused",
    )
    session.add(user)
    await session.commit()
    return user.id


def _new_job(user_id: str, **overrides) -> NewAiJob:
    values = {
        "id": str(uuid4()),
        "user_id": user_id,
        "job_type": AiJobType.PARSE_TEXT,
        "language": "en",
        "request_payload": {"text": "two eggs"},
        "request_fingerprint": "f" * 64,
    }
    values.update(overrides)
    return NewAiJob(**values)


async def _in_tx(factory, operation):
    async with factory() as session:
        result = await operation(AsyncAiJobRepository(session))
        await session.commit()
        return result


async def _enqueue(factory, user_id: str, **overrides):
    job, created = await _in_tx(
        factory, lambda repo: repo.create(_new_job(user_id, **overrides))
    )
    assert created
    return job


async def _claim(factory, *, lease_seconds: int = 120, capacity: int = 4):
    return await _in_tx(
        factory,
        lambda repo: repo.claim_next(
            lease_seconds=lease_seconds, global_capacity=capacity
        ),
    )


async def _row(factory, job_id: str) -> AiJobORM:
    async with factory() as session:
        return (
            await session.execute(select(AiJobORM).where(AiJobORM.id == job_id))
        ).scalar_one()


async def _shift(factory, job_id: str, **columns: timedelta) -> None:
    """Move timestamps relative to the database clock (negative = past)."""
    async with factory() as session:
        await session.execute(
            update(AiJobORM)
            .where(AiJobORM.id == job_id)
            .values(
                **{
                    name: func.clock_timestamp() + offset
                    for name, offset in columns.items()
                }
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_schema_ready_needs_both_ai_jobs_and_token_columns(pg_session):
    repo = AsyncAiJobRepository(pg_session)
    assert await repo.schema_ready() is True

    # DDL is transactional, so each half of the migration can be undone in turn.
    for ddl in (
        "ALTER TABLE ai_jobs RENAME TO ai_jobs_unmigrated",
        "ALTER TABLE user_fcm_tokens RENAME COLUMN last_registered_at"
        " TO last_registered_at_unmigrated",
    ):
        savepoint = await pg_session.begin_nested()
        await pg_session.execute(text(ddl))
        assert await repo.schema_ready() is False
        await savepoint.rollback()

    assert await repo.schema_ready() is True


@pytest.mark.asyncio
async def test_create_is_idempotent_by_client_id(pg_session, async_session_factory):
    user_id = await _user(pg_session)
    new_job = _new_job(user_id)

    first, created = await _in_tx(
        async_session_factory, lambda repo: repo.create(new_job)
    )
    again, created_again = await _in_tx(
        async_session_factory, lambda repo: repo.create(new_job)
    )

    assert created is True and created_again is False
    assert first.status == AiJobStatus.QUEUED and first.attempts == 0
    assert again.id == first.id and again.created_at == first.created_at


@pytest.mark.asyncio
async def test_claim_complete_and_capacity(pg_session, async_session_factory):
    user_id = await _user(pg_session)
    first = await _enqueue(async_session_factory, user_id)
    second = await _enqueue(async_session_factory, user_id)

    claim = await _claim(async_session_factory, capacity=1)
    assert claim is not None and claim.id == first.id and claim.attempts == 1
    # One non-chat job is running and capacity is 1.
    assert await _claim(async_session_factory, capacity=1) is None

    done = await _in_tx(
        async_session_factory,
        lambda repo: repo.complete(claim, result={"items": []}, notify_delay_seconds=8),
    )
    assert done is True
    row = await _row(async_session_factory, first.id)
    assert row.status == "completed" and row.result_payload == {"items": []}
    assert row.claim_token is None and row.notify_after > row.completed_at

    next_claim = await _claim(async_session_factory, capacity=1)
    assert next_claim is not None and next_claim.id == second.id


@pytest.mark.asyncio
async def test_running_chat_turns_do_not_use_worker_capacity(
    pg_session, async_session_factory
):
    user_id = await _user(pg_session)
    chat = _new_job(user_id, job_type=AiJobType.CHAT, dedupe_key="turn-1")
    assert await _in_tx(
        async_session_factory, lambda repo: repo.start_chat_job(chat, lease_seconds=90)
    )
    scan = await _enqueue(async_session_factory, user_id, job_type=AiJobType.MEAL_SCAN)

    claim = await _claim(async_session_factory, capacity=1)

    assert claim is not None and claim.id == scan.id


@pytest.mark.asyncio
async def test_stale_claim_cannot_write_after_lease_is_reclaimed(
    pg_session, async_session_factory
):
    user_id = await _user(pg_session)
    job = await _enqueue(async_session_factory, user_id)
    stale = await _claim(async_session_factory)
    await _shift(async_session_factory, job.id, lease_expires_at=timedelta(seconds=-1))

    fresh = await _claim(async_session_factory)

    assert fresh is not None and fresh.id == job.id
    assert fresh.attempts == 2 and fresh.claim_token != stale.claim_token
    assert not await _in_tx(
        async_session_factory,
        lambda repo: repo.complete(stale, result={}, notify_delay_seconds=0),
    )
    assert (
        await _in_tx(
            async_session_factory,
            lambda repo: repo.fail_attempt(
                stale, error=TRANSIENT, retry_delay_seconds=None, notify_delay_seconds=0
            ),
        )
        is None
    )
    heartbeat = await _in_tx(
        async_session_factory, lambda repo: repo.heartbeat(fresh, lease_seconds=120)
    )
    assert heartbeat.renewed and not heartbeat.cancel_requested


@pytest.mark.asyncio
async def test_lost_lease_on_last_attempt_fails_visibly(
    pg_session, async_session_factory
):
    user_id = await _user(pg_session)
    job = await _enqueue(async_session_factory, user_id, max_attempts=1)
    assert await _claim(async_session_factory)
    await _shift(async_session_factory, job.id, lease_expires_at=timedelta(seconds=-1))

    assert await _claim(async_session_factory) is None

    stored = await _in_tx(
        async_session_factory, lambda repo: repo.get(user_id=user_id, job_id=job.id)
    )
    assert stored.status == AiJobStatus.FAILED
    assert stored.error == AiJobError(
        "WORKER_INTERRUPTED", AiJobErrorKind.TRANSIENT, True
    )
    assert (await _row(async_session_factory, job.id)).notify_after is not None


@pytest.mark.asyncio
async def test_transient_failure_requeues_with_backoff_then_fails(
    pg_session, async_session_factory
):
    user_id = await _user(pg_session)
    job = await _enqueue(async_session_factory, user_id, max_attempts=2)
    claim = await _claim(async_session_factory)

    status = await _in_tx(
        async_session_factory,
        lambda repo: repo.fail_attempt(
            claim, error=TRANSIENT, retry_delay_seconds=60, notify_delay_seconds=8
        ),
    )

    assert status == AiJobStatus.QUEUED
    queued = await _in_tx(
        async_session_factory, lambda repo: repo.get(user_id=user_id, job_id=job.id)
    )
    assert queued.error == TRANSIENT
    assert queued.next_attempt_at is not None
    assert await _claim(async_session_factory) is None  # backoff not elapsed

    await _shift(async_session_factory, job.id, available_at=timedelta(seconds=-1))
    retry = await _claim(async_session_factory)
    assert retry is not None and retry.attempts == 2
    running = await _in_tx(
        async_session_factory, lambda repo: repo.get(user_id=user_id, job_id=job.id)
    )
    assert running.error is None

    final = await _in_tx(
        async_session_factory,
        lambda repo: repo.fail_attempt(
            retry, error=TRANSIENT, retry_delay_seconds=None, notify_delay_seconds=8
        ),
    )
    assert final == AiJobStatus.FAILED
    row = await _row(async_session_factory, job.id)
    assert row.completed_at is not None and row.notify_after > row.completed_at


@pytest.mark.asyncio
async def test_manual_retry_allows_exactly_one_more_attempt(
    pg_session, async_session_factory
):
    user_id = await _user(pg_session)
    job = await _enqueue(async_session_factory, user_id, max_attempts=1)
    claim = await _claim(async_session_factory)
    await _in_tx(
        async_session_factory,
        lambda repo: repo.fail_attempt(
            claim, error=TRANSIENT, retry_delay_seconds=None, notify_delay_seconds=0
        ),
    )

    retried = await _in_tx(
        async_session_factory,
        lambda repo: repo.request_retry(user_id=user_id, job_id=job.id),
    )

    assert retried.status == AiJobStatus.QUEUED
    assert retried.error is None and retried.max_attempts == 2
    assert (await _row(async_session_factory, job.id)).notify_after is None
    again = await _claim(async_session_factory)
    assert again is not None and again.attempts == 2 == again.max_attempts
    with pytest.raises(AiJobNotRetryableError):
        await _in_tx(
            async_session_factory,
            lambda repo: repo.request_retry(user_id=user_id, job_id=job.id),
        )


@pytest.mark.asyncio
async def test_manual_retry_rejects_permanent_and_foreign_jobs(
    pg_session, async_session_factory
):
    owner = await _user(pg_session, "owner")
    stranger = await _user(pg_session, "stranger")
    job = await _enqueue(async_session_factory, owner)
    claim = await _claim(async_session_factory)
    await _in_tx(
        async_session_factory,
        lambda repo: repo.fail_attempt(
            claim, error=PERMANENT, retry_delay_seconds=None, notify_delay_seconds=0
        ),
    )

    with pytest.raises(AiJobNotRetryableError):
        await _in_tx(
            async_session_factory,
            lambda repo: repo.request_retry(user_id=owner, job_id=job.id),
        )
    with pytest.raises(AiJobNotFoundError):
        await _in_tx(
            async_session_factory,
            lambda repo: repo.request_retry(user_id=stranger, job_id=job.id),
        )
    assert (
        await _in_tx(
            async_session_factory,
            lambda repo: repo.get(user_id=stranger, job_id=job.id),
        )
        is None
    )


@pytest.mark.asyncio
async def test_cancel_queued_and_running_jobs(pg_session, async_session_factory):
    user_id = await _user(pg_session)
    running_job = await _enqueue(async_session_factory, user_id)
    claim = await _claim(async_session_factory)
    queued_job = await _enqueue(async_session_factory, user_id)

    cancelled = await _in_tx(
        async_session_factory,
        lambda repo: repo.request_cancel(user_id=user_id, job_id=queued_job.id),
    )
    flagged = await _in_tx(
        async_session_factory,
        lambda repo: repo.request_cancel(user_id=user_id, job_id=running_job.id),
    )

    assert cancelled.status == AiJobStatus.CANCELLED
    assert cancelled.completed_at is not None
    assert flagged.status == AiJobStatus.RUNNING
    assert flagged.cancel_requested_at is not None
    heartbeat = await _in_tx(
        async_session_factory, lambda repo: repo.heartbeat(claim, lease_seconds=120)
    )
    assert heartbeat.renewed and heartbeat.cancel_requested
    assert await _in_tx(async_session_factory, lambda repo: repo.cancel_claimed(claim))
    row = await _row(async_session_factory, running_job.id)
    assert row.status == "cancelled" and row.notify_after is None
    unchanged = await _in_tx(
        async_session_factory,
        lambda repo: repo.request_cancel(user_id=user_id, job_id=running_job.id),
    )
    assert unchanged.status == AiJobStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_request_wins_over_failure_and_dead_workers(
    pg_session, async_session_factory
):
    user_id = await _user(pg_session)
    failing = await _enqueue(async_session_factory, user_id)
    failing_claim = await _claim(async_session_factory)
    orphan = await _enqueue(async_session_factory, user_id)
    assert await _claim(async_session_factory)
    for job in (failing, orphan):
        await _in_tx(
            async_session_factory,
            lambda repo, job=job: repo.request_cancel(user_id=user_id, job_id=job.id),
        )

    status = await _in_tx(
        async_session_factory,
        lambda repo: repo.fail_attempt(
            failing_claim,
            error=TRANSIENT,
            retry_delay_seconds=5,
            notify_delay_seconds=0,
        ),
    )
    await _shift(
        async_session_factory, orphan.id, lease_expires_at=timedelta(seconds=-1)
    )
    assert await _claim(async_session_factory) is None

    assert status == AiJobStatus.CANCELLED
    assert (await _row(async_session_factory, orphan.id)).status == "cancelled"


@pytest.mark.asyncio
async def test_completion_after_cancel_request_is_kept_but_not_announced(
    pg_session, async_session_factory
):
    user_id = await _user(pg_session)
    job = await _enqueue(async_session_factory, user_id)
    claim = await _claim(async_session_factory)
    await _in_tx(
        async_session_factory,
        lambda repo: repo.request_cancel(user_id=user_id, job_id=job.id),
    )

    assert await _in_tx(
        async_session_factory,
        lambda repo: repo.complete(
            claim, result={"meal_id": "m"}, notify_delay_seconds=0
        ),
    )

    row = await _row(async_session_factory, job.id)
    assert row.status == "completed" and row.notify_after is None


@pytest.mark.asyncio
async def test_seen_and_listing_states(pg_session, async_session_factory):
    user_id = await _user(pg_session)
    other = await _user(pg_session, "other")
    done = await _enqueue(async_session_factory, user_id)
    done_claim = await _claim(async_session_factory)
    await _in_tx(
        async_session_factory,
        lambda repo: repo.complete(done_claim, result={}, notify_delay_seconds=8),
    )
    active = await _enqueue(async_session_factory, user_id)
    cancelled = await _enqueue(async_session_factory, user_id)
    await _in_tx(
        async_session_factory,
        lambda repo: repo.request_cancel(user_id=user_id, job_id=cancelled.id),
    )
    await _enqueue(async_session_factory, other)
    old = await _enqueue(async_session_factory, user_id)
    await _shift(async_session_factory, old.id, created_at=timedelta(days=-2))
    since = (await _row(async_session_factory, done.id)).created_at - timedelta(hours=1)

    async def ids(state):
        jobs = await _in_tx(
            async_session_factory,
            lambda repo: repo.list_by_state(
                user_id=user_id, state=state, since=since, limit=20
            ),
        )
        return [job.id for job in jobs]

    assert await ids(AiJobListState.OPEN) == [active.id, done.id]
    assert await ids(AiJobListState.ACTIVE) == [active.id]
    assert await ids(AiJobListState.UNSEEN) == [done.id]

    seen = await _in_tx(
        async_session_factory,
        lambda repo: repo.mark_seen(
            user_id=user_id, job_ids=[done.id, active.id, done.id]
        ),
    )
    assert seen == 1
    assert (
        await _in_tx(
            async_session_factory,
            lambda repo: repo.mark_seen(user_id=user_id, job_ids=[done.id]),
        )
        == 0
    )
    assert (await _row(async_session_factory, done.id)).notify_after is None
    assert await ids(AiJobListState.OPEN) == [active.id]
    by_ids = await _in_tx(
        async_session_factory,
        lambda repo: repo.list_by_ids(user_id=user_id, job_ids=[done.id, "missing"]),
    )
    assert [job.id for job in by_ids] == [done.id]


@pytest.mark.asyncio
async def test_notifications_are_reserved_once_and_can_be_released(
    pg_session, async_session_factory
):
    user_id = await _user(pg_session)
    job = await _enqueue(async_session_factory, user_id, job_type=AiJobType.MEAL_SCAN)
    claim = await _claim(async_session_factory)
    await _in_tx(
        async_session_factory,
        lambda repo: repo.complete(
            claim, result={"meal_id": "meal-1"}, notify_delay_seconds=60
        ),
    )

    async def reserve():
        return await _in_tx(
            async_session_factory,
            lambda repo: repo.claim_notifications(
                limit=10, max_attempts=3, stale_after_seconds=3600
            ),
        )

    assert await reserve() == []  # still inside the grace window
    await _shift(async_session_factory, job.id, notify_after=timedelta(seconds=-1))
    [reserved] = await reserve()
    assert reserved.job_id == job.id and reserved.status == AiJobStatus.COMPLETED
    assert reserved.result == {"meal_id": "meal-1"} and reserved.notify_attempts == 1
    assert await reserve() == []

    await _in_tx(
        async_session_factory,
        lambda repo: repo.release_notification(job_id=job.id, retry_delay_seconds=0),
    )
    [again] = await reserve()
    assert again.notify_attempts == 2


@pytest.mark.asyncio
async def test_notifications_skip_seen_stale_and_exhausted_jobs(
    pg_session, async_session_factory
):
    user_id = await _user(pg_session)
    jobs = []
    for _ in range(3):
        job = await _enqueue(async_session_factory, user_id)
        claim = await _claim(async_session_factory)
        await _in_tx(
            async_session_factory,
            lambda repo, claim=claim: repo.fail_attempt(
                claim, error=PERMANENT, retry_delay_seconds=None, notify_delay_seconds=0
            ),
        )
        jobs.append(job)
    seen, stale, exhausted = jobs
    await _in_tx(
        async_session_factory,
        lambda repo: repo.mark_seen(user_id=user_id, job_ids=[seen.id]),
    )
    await _shift(async_session_factory, stale.id, notify_after=timedelta(hours=-2))
    async with async_session_factory() as session:
        await session.execute(
            update(AiJobORM)
            .where(AiJobORM.id == exhausted.id)
            .values(notify_attempts=3)
        )
        await session.commit()

    reserved = await _in_tx(
        async_session_factory,
        lambda repo: repo.claim_notifications(
            limit=10, max_attempts=3, stale_after_seconds=3600
        ),
    )

    assert reserved == []
    for job in jobs:
        assert (await _row(async_session_factory, job.id)).notify_after is None


@pytest.mark.asyncio
async def test_chat_job_start_takeover_and_conflict(pg_session, async_session_factory):
    user_id = await _user(pg_session)
    chat = _new_job(user_id, job_type=AiJobType.CHAT, dedupe_key="turn-1")

    claim = await _in_tx(
        async_session_factory, lambda repo: repo.start_chat_job(chat, lease_seconds=90)
    )
    assert claim is not None and claim.attempts == 1
    assert (
        await _in_tx(
            async_session_factory,
            lambda repo: repo.find_active_chat_job_id(
                user_id=user_id, dedupe_key="turn-1"
            ),
        )
        == chat.id
    )
    # Already streaming in a live process.
    assert (
        await _in_tx(
            async_session_factory,
            lambda repo: repo.start_chat_job(chat, lease_seconds=90),
        )
        is None
    )
    await _in_tx(
        async_session_factory,
        lambda repo: repo.fail_attempt(
            claim, error=TRANSIENT, retry_delay_seconds=None, notify_delay_seconds=0
        ),
    )

    takeover = await _in_tx(
        async_session_factory, lambda repo: repo.start_chat_job(chat, lease_seconds=90)
    )

    assert takeover is not None and takeover.attempts == 2
    assert takeover.claim_token != claim.claim_token
    row = await _row(async_session_factory, chat.id)
    assert row.status == "running" and row.error_code is None
    with pytest.raises(AiJobConflictError):
        await _in_tx(
            async_session_factory,
            lambda repo: repo.start_chat_job(
                _new_job(
                    user_id,
                    id=chat.id,
                    job_type=AiJobType.CHAT,
                    request_fingerprint="0" * 64,
                ),
                lease_seconds=90,
            ),
        )


@pytest.mark.asyncio
async def test_concurrent_workers_never_claim_the_same_job(
    pg_session, async_session_factory
):
    user_id = await _user(pg_session)
    jobs = [await _enqueue(async_session_factory, user_id) for _ in range(3)]

    claims = await asyncio.gather(*(_claim(async_session_factory) for _ in range(5)))

    claimed = [claim.id for claim in claims if claim is not None]
    assert sorted(claimed) == sorted(job.id for job in jobs)


@pytest.mark.asyncio
async def test_push_tokens_move_between_users_and_deactivate(
    pg_session, async_session_factory
):
    alice = await _user(pg_session, "alice")
    bob = await _user(pg_session, "bob")
    async with async_session_factory() as session:
        session.add(
            UserFcmTokenORM(
                user_id=alice, fcm_token="legacy", device_type="ios", is_active=True
            )
        )
        await session.commit()

    async def tokens(operation):
        async with async_session_factory() as session:
            result = await operation(AsyncPushTokenRepository(session))
            await session.commit()
            return result

    await tokens(
        lambda repo: repo.upsert(user_id=alice, token="t1", device_type=DeviceType.IOS)
    )
    await tokens(
        lambda repo: repo.upsert(
            user_id=alice, token="t2", device_type=DeviceType.ANDROID
        )
    )
    await tokens(
        lambda repo: repo.upsert(user_id=bob, token="t1", device_type=DeviceType.IOS)
    )

    alice_targets = await tokens(lambda repo: repo.list_active_targets(user_id=alice))
    bob_targets = await tokens(lambda repo: repo.list_active_targets(user_id=bob))
    assert [target.token for target in alice_targets] == ["t2"]
    assert [(t.token, t.device_type) for t in bob_targets] == [("t1", DeviceType.IOS)]

    assert (
        await tokens(lambda repo: repo.deactivate(user_id=alice, token="t1")) is False
    )
    assert await tokens(lambda repo: repo.deactivate(user_id=bob, token="t1")) is True
    assert await tokens(lambda repo: repo.deactivate_tokens(["t2", "t2", "nope"])) == 1
    assert await tokens(lambda repo: repo.list_active_targets(user_id=alice)) == []
    await tokens(
        lambda repo: repo.upsert(user_id=bob, token="t1", device_type=DeviceType.IOS)
    )
    assert len(await tokens(lambda repo: repo.list_active_targets(user_id=bob))) == 1
