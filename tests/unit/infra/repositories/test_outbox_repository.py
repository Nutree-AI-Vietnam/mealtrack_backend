"""Comprehensive unit test suite for AsyncOutboxRepository and UoW integration."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from tests.fixtures.fakes.fake_outbox_repository import FakeOutboxRepository
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.domain.models.outbox_status import OutboxEvent, OutboxStatus
from src.domain.ports.outbox_handler_port import OutboxHandlerResult
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.outbox_event import TransactionalOutboxORM
from src.infra.repositories.outbox_repository import AsyncOutboxRepository


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeNestedContext:
    def __init__(self, session, should_raise=False):
        self._session = session
        self._should_raise = should_raise

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._should_raise:
            raise IntegrityError(
                "duplicate key",
                params=None,
                orig=Exception("uq_outbox_event_id"),
            )
        return False


class _MockAsyncSession:
    def __init__(self, execute_results=None, fail_nested=False):
        self.execute_results = list(execute_results or [])
        self.added = []
        self.flushed = False
        self.executed_statements = []
        self.fail_nested = fail_nested

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True
        if self.fail_nested:
            raise IntegrityError(
                "duplicate key",
                params=None,
                orig=Exception("uq_outbox_event_id"),
            )

    def begin_nested(self):
        return _FakeNestedContext(self, should_raise=self.fail_nested)

    async def execute(self, statement):
        self.executed_statements.append(statement)
        if self.execute_results:
            return self.execute_results.pop(0)
        return _FakeResult([])


# ---------------------------------------------------------------------------
# Domain Model Tests
# ---------------------------------------------------------------------------


def test_outbox_event_is_claimable():
    now = utc_now()
    event_pending_due = OutboxEvent(
        status=OutboxStatus.PENDING,
        next_retry_at=now - timedelta(seconds=1),
    )
    assert event_pending_due.is_claimable(now) is True

    event_pending_future = OutboxEvent(
        status=OutboxStatus.PENDING,
        next_retry_at=now + timedelta(seconds=60),
    )
    assert event_pending_future.is_claimable(now) is False

    event_in_progress_expired = OutboxEvent(
        status=OutboxStatus.IN_PROGRESS,
        lease_expires_at=now - timedelta(seconds=1),
    )
    assert event_in_progress_expired.is_claimable(now) is True

    event_in_progress_active = OutboxEvent(
        status=OutboxStatus.IN_PROGRESS,
        lease_expires_at=now + timedelta(seconds=60),
    )
    assert event_in_progress_active.is_claimable(now) is False


def test_outbox_event_is_terminal():
    event_pending = OutboxEvent(status=OutboxStatus.PENDING)
    assert event_pending.is_terminal() is False

    event_in_progress = OutboxEvent(status=OutboxStatus.IN_PROGRESS)
    assert event_in_progress.is_terminal() is False

    event_completed = OutboxEvent(status=OutboxStatus.COMPLETED)
    assert event_completed.is_terminal() is True

    event_dlq = OutboxEvent(status=OutboxStatus.FAILED_DEAD_LETTER)
    assert event_dlq.is_terminal() is True


def test_to_domain_and_from_domain_conversions():
    now = utc_now()
    event = OutboxEvent(
        id="test-id",
        event_id="evt-123",
        event_type="affiliate.conversion",
        aggregate_type="Order",
        aggregate_id="ord-456",
        payload={"amount": 99.99},
        status=OutboxStatus.PENDING,
        retry_count=2,
        max_retries=10,
        next_retry_at=now,
        lease_owner="worker-1",
        lease_expires_at=now + timedelta(minutes=1),
        error_log=[{"attempt": 1, "error": "timeout"}],
        created_at=now,
        updated_at=now,
        processed_at=None,
    )

    orm = TransactionalOutboxORM.from_domain(event)
    assert orm.id == "test-id"
    assert orm.event_id == "evt-123"
    assert orm.event_type == "affiliate.conversion"
    assert orm.status == "PENDING"
    assert orm.payload == {"amount": 99.99}

    domain_obj = orm.to_domain()
    assert domain_obj.id == "test-id"
    assert domain_obj.event_id == "evt-123"
    assert domain_obj.status == OutboxStatus.PENDING
    assert domain_obj.retry_count == 2
    assert domain_obj.max_retries == 10


# ---------------------------------------------------------------------------
# Enqueue Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_creates_pending_record_with_defaults():
    session = _MockAsyncSession()
    repo = AsyncOutboxRepository(session)

    result = await repo.enqueue(
        event_type="test.event",
        payload={"key": "value"},
    )

    assert result is not None
    assert len(session.added) == 1
    assert session.flushed is True

    persisted = session.added[0]
    assert persisted.event_type == "test.event"
    assert persisted.payload == {"key": "value"}
    assert persisted.status == OutboxStatus.PENDING.value
    assert persisted.retry_count == 0
    assert persisted.max_retries == 5
    assert persisted.lease_owner is None
    assert persisted.lease_expires_at is None
    assert persisted.error_log == []


@pytest.mark.asyncio
async def test_enqueue_with_custom_parameters():
    session = _MockAsyncSession()
    repo = AsyncOutboxRepository(session)
    sched = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    result = await repo.enqueue(
        event_type="affiliate.conversion",
        payload={"order_id": "123"},
        event_id="evt-custom-1",
        max_retries=10,
        scheduled_at=sched,
        aggregate_type="Order",
        aggregate_id="ord-123",
    )

    assert result is not None
    persisted = session.added[0]
    assert persisted.event_id == "evt-custom-1"
    assert persisted.max_retries == 10
    assert persisted.next_retry_at == sched
    assert persisted.aggregate_type == "Order"
    assert persisted.aggregate_id == "ord-123"


@pytest.mark.asyncio
async def test_enqueue_duplicate_event_id_swallowed_by_savepoint():
    session = _MockAsyncSession(fail_nested=True)
    repo = AsyncOutboxRepository(session)

    result = await repo.enqueue(
        event_type="test.duplicate",
        payload={"foo": "bar"},
        event_id="duplicate-id",
    )

    assert result is None


# ---------------------------------------------------------------------------
# Claim Due Records Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_due_records_claims_pending_and_stale_leases():
    now = utc_now()
    row1 = TransactionalOutboxORM(
        id="outbox-1",
        event_id="evt-1",
        event_type="push.notify",
        payload={},
        status=OutboxStatus.PENDING.value,
        retry_count=0,
        max_retries=5,
        next_retry_at=now - timedelta(seconds=10),
    )
    row2 = TransactionalOutboxORM(
        id="outbox-2",
        event_id="evt-2",
        event_type="telemetry.track",
        payload={},
        status=OutboxStatus.IN_PROGRESS.value,
        retry_count=1,
        max_retries=5,
        lease_owner="dead_worker",
        lease_expires_at=now - timedelta(seconds=5),
    )

    session = _MockAsyncSession(execute_results=[_FakeResult([row1, row2])])
    repo = AsyncOutboxRepository(session)

    claimed = await repo.claim_due_records(
        worker_id="worker-node-1",
        batch_size=50,
        lease_duration=timedelta(seconds=45),
        now=now,
    )

    assert len(claimed) == 2
    assert session.flushed is True

    for row in claimed:
        assert row.status == OutboxStatus.IN_PROGRESS.value
        assert row.lease_owner == "worker-node-1"
        assert row.lease_expires_at is not None
        assert row.lease_expires_at > now


# ---------------------------------------------------------------------------
# Mark Completed Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_completed_updates_status_and_clears_lease():
    session = _MockAsyncSession()
    repo = AsyncOutboxRepository(session)

    await repo.mark_completed("outbox-123")

    assert len(session.executed_statements) == 1
    assert session.flushed is True
    sql_str = str(session.executed_statements[0])
    assert "UPDATE outbox_events" in sql_str or "outbox_events" in sql_str


# ---------------------------------------------------------------------------
# Record Failure Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_failure_transient_reschedules_with_backoff():
    row = TransactionalOutboxORM(
        id="outbox-1",
        event_id="evt-1",
        event_type="push.notify",
        payload={},
        status=OutboxStatus.IN_PROGRESS.value,
        retry_count=0,
        max_retries=5,
        lease_owner="worker-1",
        lease_expires_at=utc_now() + timedelta(minutes=1),
        error_log=[],
    )
    session = _MockAsyncSession(execute_results=[_FakeResult([row])])
    repo = AsyncOutboxRepository(session)

    result = OutboxHandlerResult.transient_failure(
        error_message="503 Service Unavailable",
        error_type="HttpServerError",
        status_code=503,
    )

    is_dlq = await repo.record_failure("outbox-1", result)

    assert is_dlq is False
    assert row.retry_count == 1
    assert row.status == OutboxStatus.PENDING.value
    assert row.lease_owner is None
    assert row.lease_expires_at is None
    assert len(row.error_log) == 1
    assert row.error_log[0]["error_type"] == "HttpServerError"
    assert row.error_log[0]["is_transient"] is True
    assert row.next_retry_at > utc_now()


@pytest.mark.asyncio
async def test_record_failure_paused_keeps_retry_count_and_pending_state():
    row = TransactionalOutboxORM(
        id="outbox-1",
        event_id="evt-1",
        event_type="hydration.created.v1",
        payload={},
        status=OutboxStatus.IN_PROGRESS.value,
        retry_count=4,
        max_retries=5,
        lease_owner="worker-1",
        lease_expires_at=utc_now() + timedelta(minutes=1),
        error_log=[],
    )
    session = _MockAsyncSession(execute_results=[_FakeResult([row])])
    repo = AsyncOutboxRepository(session)

    is_dlq = await repo.record_failure(
        "outbox-1", OutboxHandlerResult.paused("queue disabled")
    )

    assert is_dlq is False
    assert row.retry_count == 4
    assert row.status == OutboxStatus.PENDING.value
    assert row.lease_owner is None
    assert row.lease_expires_at is None
    assert row.next_retry_at > utc_now()


@pytest.mark.asyncio
async def test_record_failure_permanent_moves_to_dead_letter():
    row = TransactionalOutboxORM(
        id="outbox-1",
        event_id="evt-1",
        event_type="push.notify",
        payload={},
        status=OutboxStatus.IN_PROGRESS.value,
        retry_count=0,
        max_retries=5,
        lease_owner="worker-1",
        error_log=[],
    )
    session = _MockAsyncSession(execute_results=[_FakeResult([row])])
    repo = AsyncOutboxRepository(session)

    result = OutboxHandlerResult.permanent_failure(
        error_message="400 Bad Request: Invalid Token",
        error_type="InvalidRecipientError",
        status_code=400,
    )

    is_dlq = await repo.record_failure("outbox-1", result)

    assert is_dlq is True
    assert row.retry_count == 1
    assert row.status == OutboxStatus.FAILED_DEAD_LETTER.value
    assert row.lease_owner is None
    assert row.lease_expires_at is None
    assert len(row.error_log) == 1
    assert row.error_log[0]["is_transient"] is False


@pytest.mark.asyncio
async def test_record_failure_max_retries_exhaustion_moves_to_dead_letter():
    row = TransactionalOutboxORM(
        id="outbox-1",
        event_id="evt-1",
        event_type="push.notify",
        payload={},
        status=OutboxStatus.IN_PROGRESS.value,
        retry_count=4,  # Next attempt becomes 5 == max_retries
        max_retries=5,
        lease_owner="worker-1",
        error_log=[],
    )
    session = _MockAsyncSession(execute_results=[_FakeResult([row])])
    repo = AsyncOutboxRepository(session)

    result = OutboxHandlerResult.transient_failure(
        error_message="Connection reset by peer",
        error_type="NetworkError",
    )

    is_dlq = await repo.record_failure("outbox-1", result)

    assert is_dlq is True
    assert row.retry_count == 5
    assert row.status == OutboxStatus.FAILED_DEAD_LETTER.value


@pytest.mark.asyncio
async def test_record_failure_caps_error_log_at_ten_entries():
    initial_log = [{"attempt": i, "error": "err"} for i in range(1, 10)]
    row = TransactionalOutboxORM(
        id="outbox-1",
        event_id="evt-1",
        event_type="push.notify",
        payload={},
        status=OutboxStatus.IN_PROGRESS.value,
        retry_count=9,
        max_retries=20,
        error_log=initial_log,
    )
    session = _MockAsyncSession(
        execute_results=[_FakeResult([row]), _FakeResult([row])]
    )
    repo = AsyncOutboxRepository(session)

    result = OutboxHandlerResult.transient_failure("error 10")
    await repo.record_failure("outbox-1", result)
    assert len(row.error_log) == 10

    result2 = OutboxHandlerResult.transient_failure("error 11")
    await repo.record_failure("outbox-1", result2)
    assert len(row.error_log) == 10
    assert row.error_log[-1]["attempt"] == 11
    assert row.error_log[0]["attempt"] == 2  # Oldest evicted


@pytest.mark.asyncio
async def test_record_failure_nonexistent_returns_false():
    session = _MockAsyncSession(execute_results=[_FakeResult([])])
    repo = AsyncOutboxRepository(session)

    result = OutboxHandlerResult.transient_failure("not found")
    is_dlq = await repo.record_failure("nonexistent-id", result)
    assert is_dlq is False


# ---------------------------------------------------------------------------
# Cleanup Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_outbox_records_purges_chunked_batches():
    completed_ids = ["c-1", "c-2"]
    dlq_ids = ["d-1"]

    session = _MockAsyncSession(
        execute_results=[
            _FakeResult(completed_ids),
            _FakeResult([]),  # execute delete
            _FakeResult(dlq_ids),
            _FakeResult([]),  # execute delete
        ]
    )
    repo = AsyncOutboxRepository(session)

    summary = await repo.cleanup_outbox_records(
        completed_older_than=utc_now() - timedelta(days=7),
        dead_letter_older_than=utc_now() - timedelta(days=30),
        batch_size=100,
    )

    assert summary["deleted_completed"] == 2
    assert summary["deleted_dead_letter"] == 1
    assert summary["total_deleted"] == 3
    assert session.flushed is True


# ---------------------------------------------------------------------------
# Find & Get Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_by_id_and_find_by_event_id():
    row = TransactionalOutboxORM(id="out-1", event_id="evt-1", event_type="t")
    session = _MockAsyncSession(
        execute_results=[_FakeResult([row]), _FakeResult([row])]
    )
    repo = AsyncOutboxRepository(session)

    found_by_id = await repo.find_by_id("out-1")
    assert found_by_id is not None
    assert found_by_id.id == "out-1"

    found_by_event_id = await repo.find_by_event_id("evt-1")
    assert found_by_event_id is not None
    assert found_by_event_id.event_id == "evt-1"

    # Also test get_by_* aliases
    session.execute_results = [_FakeResult([row]), _FakeResult([row])]
    get_id = await repo.get_by_id("out-1")
    assert get_id is not None
    get_evt = await repo.get_by_event_id("evt-1")
    assert get_evt is not None


# ---------------------------------------------------------------------------
# Fake Repository & Unit of Work Integration Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_outbox_repository_lifecycle():
    fake_repo = FakeOutboxRepository()

    # Enqueue
    event = await fake_repo.enqueue("test.fake", {"foo": "bar"}, event_id="fake-evt-1")
    assert event is not None
    assert event.status == OutboxStatus.PENDING

    # Duplicate rejected
    dup = await fake_repo.enqueue("test.fake", {}, event_id="fake-evt-1")
    assert dup is None

    # Claim
    claimed = await fake_repo.claim_due_records(worker_id="w-1", batch_size=10)
    assert len(claimed) == 1
    assert claimed[0].status == OutboxStatus.IN_PROGRESS
    assert claimed[0].lease_owner == "w-1"

    # Mark completed
    await fake_repo.mark_completed(claimed[0].id)
    assert fake_repo.events[claimed[0].id].status == OutboxStatus.COMPLETED

    # Cleanup
    summary = await fake_repo.cleanup_outbox_records(
        completed_older_than=utc_now() + timedelta(hours=1),
        dead_letter_older_than=utc_now() + timedelta(hours=1),
    )
    assert summary["deleted_completed"] == 1
    assert len(fake_repo.events) == 0


@pytest.mark.asyncio
async def test_fake_outbox_repository_failure_and_dlq():
    fake_repo = FakeOutboxRepository()
    event = await fake_repo.enqueue("test.fail", {}, max_retries=2)
    assert event is not None

    # Transient failure (retry 1 of 2)
    res_transient = OutboxHandlerResult.transient_failure(
        "Network glitch", error_type="NetworkError"
    )
    is_dlq = await fake_repo.record_failure(event.id, res_transient)
    assert is_dlq is False
    assert event.status == OutboxStatus.PENDING
    assert event.retry_count == 1

    # Transient failure exhausting retries (retry 2 of 2)
    is_dlq2 = await fake_repo.record_failure(event.id, res_transient)
    assert is_dlq2 is True
    assert event.status == OutboxStatus.FAILED_DEAD_LETTER
    assert event.retry_count == 2

    # Permanent failure on new event
    event2 = await fake_repo.enqueue("test.perm", {})
    assert event2 is not None
    res_perm = OutboxHandlerResult.permanent_failure("Invalid schema")
    is_dlq_perm = await fake_repo.record_failure(event2.id, res_perm)
    assert is_dlq_perm is True
    assert event2.status == OutboxStatus.FAILED_DEAD_LETTER


@pytest.mark.asyncio
async def test_fake_uow_has_outbox_repository():
    uow = FakeUnitOfWork()
    assert hasattr(uow, "outbox")
    assert isinstance(uow.outbox, FakeOutboxRepository)

    async with uow:
        evt = await uow.outbox.enqueue("user.signup", {"user_id": "u1"})
        assert evt is not None
        await uow.commit()

    assert uow.committed is True
