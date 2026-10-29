"""Engagement push outbox semantics on real PostgreSQL (ON CONFLICT, SKIP LOCKED)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.domain.model.notification import (
    EngagementPushKind,
    NewEngagementPush,
)
from src.infra.database.models.notification.notification_outbox import (
    NotificationOutboxORM as Outbox,
)
from src.infra.database.models.user.user import User
from src.infra.repositories.engagement_push_repository import (
    AsyncEngagementPushRepository,
)

pytestmark = pytest.mark.integration

BILLING = EngagementPushKind.BILLING_ISSUE


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


def _push(
    user_id: str, *, due_in: timedelta = timedelta(minutes=-1), **overrides
) -> NewEngagementPush:
    scheduled = datetime.now(UTC) + due_in
    values = {
        "id": str(uuid4()),
        "user_id": user_id,
        "kind": BILLING,
        "scheduled_date": scheduled.date(),
        "scheduled_for_utc": scheduled,
        "expires_at": scheduled + timedelta(hours=48),
        "context": {"kind": "billing_issue", "language": "vi"},
    }
    values.update(overrides)
    return NewEngagementPush(**values)


async def _in_tx(factory, operation):
    async with factory() as session:
        result = await operation(AsyncEngagementPushRepository(session))
        await session.commit()
        return result


async def _enqueue(factory, push: NewEngagementPush) -> bool:
    return await _in_tx(factory, lambda repo: repo.enqueue(push))


async def _status(factory, push_id: str) -> str | None:
    async with factory() as session:
        return (
            await session.execute(select(Outbox.status).where(Outbox.id == push_id))
        ).scalar_one_or_none()


async def _legacy_row(factory, user_id: str, *, expires_in: timedelta) -> str:
    """A row of a kind this repository does not own (e.g. old trial pushes)."""
    row_id = str(uuid4())
    now = datetime.now(UTC)
    async with factory() as session:
        session.add(
            Outbox(
                id=row_id,
                user_id=user_id,
                notification_type="trial_expiry_1d",
                scheduled_date=now.date(),
                scheduled_for_utc=now - timedelta(hours=1),
                status="pending",
                context={},
                expires_at=now + expires_in,
            )
        )
        await session.commit()
    return row_id


@pytest.mark.asyncio
async def test_enqueue_keeps_one_push_per_user_kind_and_local_date(
    pg_session, async_session_factory
):
    user_id = await _user(pg_session)
    push = _push(user_id, scheduled_date=date(2026, 10, 27))

    assert await _enqueue(async_session_factory, push) is True
    assert (
        await _enqueue(
            async_session_factory, _push(user_id, scheduled_date=date(2026, 10, 27))
        )
        is False
    )
    assert (
        await _enqueue(
            async_session_factory, _push(user_id, scheduled_date=date(2026, 10, 28))
        )
        is True
    )

    async with async_session_factory() as session:
        row = (
            await session.execute(select(Outbox).where(Outbox.id == push.id))
        ).scalar_one()
    assert row.status == "pending"
    assert row.notification_type == "billing_issue"
    assert row.context == {"kind": "billing_issue", "language": "vi"}
    assert row.context_schema_version == 1
    assert row.created_at is not None


@pytest.mark.asyncio
async def test_claim_takes_only_due_unexpired_engagement_pushes(
    pg_session, async_session_factory
):
    alice = await _user(pg_session, "alice")
    bob = await _user(pg_session, "bob")
    later = _push(alice, due_in=timedelta(minutes=-1))
    earlier = _push(bob, due_in=timedelta(minutes=-5))
    future = _push(alice, due_in=timedelta(hours=2), scheduled_date=date(2030, 1, 1))
    lapsed = _push(
        bob,
        due_in=timedelta(hours=-50),
        scheduled_date=date(2020, 1, 1),
    )
    for push in (later, earlier, future, lapsed):
        assert await _enqueue(async_session_factory, push)
    legacy = await _legacy_row(async_session_factory, alice, expires_in=timedelta(1))

    claims = await _in_tx(async_session_factory, lambda r: r.claim_due(limit=10))

    assert [claim.id for claim in claims] == [earlier.id, later.id]
    assert claims[0].user_id == bob
    assert claims[0].kind is BILLING
    assert claims[0].context == {"kind": "billing_issue", "language": "vi"}
    assert await _status(async_session_factory, earlier.id) == "processing"
    assert await _status(async_session_factory, future.id) == "pending"
    assert await _status(async_session_factory, lapsed.id) == "pending"
    assert await _status(async_session_factory, legacy) == "pending"
    # Nothing is claimed twice.
    assert await _in_tx(async_session_factory, lambda r: r.claim_due(limit=10)) == []


@pytest.mark.asyncio
async def test_concurrent_claims_skip_rows_another_dispatcher_holds(
    pg_session, async_session_factory
):
    pushes = []
    for name in ("a", "b", "c"):
        user_id = await _user(pg_session, name)
        pushes.append(_push(user_id))
        assert await _enqueue(async_session_factory, pushes[-1])

    async with async_session_factory() as first, async_session_factory() as second:
        held = await AsyncEngagementPushRepository(first).claim_due(limit=2)
        # The first transaction is still open: its rows are skipped, not awaited.
        rest = await AsyncEngagementPushRepository(second).claim_due(limit=2)
        await second.commit()
        await first.commit()

    assert len(held) == 2
    assert len(rest) == 1
    assert {c.id for c in held} | {c.id for c in rest} == {p.id for p in pushes}


@pytest.mark.asyncio
async def test_claim_rejects_an_invalid_batch(pg_session):
    repo = AsyncEngagementPushRepository(pg_session)

    for limit in (0, 101):
        with pytest.raises(ValueError):
            await repo.claim_due(limit=limit)


@pytest.mark.asyncio
async def test_only_a_processing_push_can_be_settled(pg_session, async_session_factory):
    user_id = await _user(pg_session)
    sent = _push(user_id, scheduled_date=date(2026, 1, 1))
    failed = _push(user_id, scheduled_date=date(2026, 1, 2))
    pending = _push(user_id, due_in=timedelta(hours=1), scheduled_date=date(2030, 1, 1))
    for push in (sent, failed, pending):
        assert await _enqueue(async_session_factory, push)
    await _in_tx(async_session_factory, lambda r: r.claim_due(limit=10))

    assert await _in_tx(async_session_factory, lambda r: r.mark_sent(sent.id))
    assert await _in_tx(async_session_factory, lambda r: r.mark_failed(failed.id))
    assert not await _in_tx(async_session_factory, lambda r: r.mark_failed(sent.id))
    assert not await _in_tx(async_session_factory, lambda r: r.mark_sent(pending.id))
    assert not await _in_tx(async_session_factory, lambda r: r.mark_sent("missing"))

    assert await _status(async_session_factory, sent.id) == "sent"
    assert await _status(async_session_factory, failed.id) == "failed"
    assert await _status(async_session_factory, pending.id) == "pending"


@pytest.mark.asyncio
async def test_expire_marks_waiting_and_stuck_pushes_past_their_expiry(
    pg_session, async_session_factory
):
    user_id = await _user(pg_session)
    stale = _push(
        user_id,
        due_in=timedelta(hours=-49),
        scheduled_date=date(2026, 1, 1),
    )
    stuck = _push(user_id, scheduled_date=date(2026, 1, 2))
    fresh = _push(user_id, scheduled_date=date(2026, 1, 3), due_in=timedelta(hours=1))
    for push in (stale, stuck, fresh):
        assert await _enqueue(async_session_factory, push)
    await _in_tx(async_session_factory, lambda r: r.claim_due(limit=10))  # stuck
    async with async_session_factory() as session:
        row = (
            await session.execute(select(Outbox).where(Outbox.id == stuck.id))
        ).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
    legacy = await _legacy_row(
        async_session_factory, user_id, expires_in=timedelta(hours=-1)
    )

    expired = await _in_tx(async_session_factory, lambda r: r.expire_stale())

    assert expired == 2
    assert await _status(async_session_factory, stale.id) == "expired"
    assert await _status(async_session_factory, stuck.id) == "expired"
    assert await _status(async_session_factory, fresh.id) == "pending"
    assert await _status(async_session_factory, legacy) == "pending"


@pytest.mark.asyncio
async def test_delete_pending_drops_only_that_users_waiting_push(
    pg_session, async_session_factory
):
    alice = await _user(pg_session, "alice")
    bob = await _user(pg_session, "bob")
    waiting = _push(alice, due_in=timedelta(hours=3), scheduled_date=date(2030, 1, 1))
    in_flight = _push(alice, scheduled_date=date(2026, 1, 1))
    bobs = _push(bob, due_in=timedelta(hours=3))
    assert await _enqueue(async_session_factory, in_flight)
    await _in_tx(async_session_factory, lambda r: r.claim_due(limit=10))
    for push in (waiting, bobs):
        assert await _enqueue(async_session_factory, push)
    legacy = await _legacy_row(async_session_factory, alice, expires_in=timedelta(1))

    deleted = await _in_tx(
        async_session_factory,
        lambda r: r.delete_pending(user_id=alice, kind=BILLING),
    )

    assert deleted == 1
    assert await _status(async_session_factory, waiting.id) is None
    assert await _status(async_session_factory, in_flight.id) == "processing"
    assert await _status(async_session_factory, bobs.id) == "pending"
    assert await _status(async_session_factory, legacy) == "pending"
