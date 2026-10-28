"""RevenueCatAccessRefreshService: read the customer, then apply it in a UoW."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.ports.subscription_access_refresh_port import (
    SubscriptionAccessApplyError,
    SubscriptionAccessRefresh,
)
from src.domain.ports.subscription_service_port import (
    RevenueCatSnapshot,
    RevenueCatSnapshotUnavailable,
)
from src.infra.services import subscription_access_refresh_service as module
from src.infra.services.subscription_access_refresh_service import (
    RevenueCatAccessRefreshService,
)
from src.infra.services.subscription_access_sync_service import (
    SubscriptionSyncOutcome,
)

SNAPSHOT = RevenueCatSnapshot(
    subscriber={"entitlements": {}, "subscriptions": {}},
    request_date=datetime(2026, 10, 26, tzinfo=UTC),
    app_user_id="firebase-uid",
)


class _Uow:
    def __init__(self, log: list, *, firebase_uid="firebase-uid", user=None):
        self.log = log
        self.session = MagicMock()
        self.session.scalar = AsyncMock(return_value=firebase_uid)
        self.session.get = AsyncMock(return_value=user)
        self.exit_error: BaseException | None = None

    async def __aenter__(self):
        self.log.append("uow_enter")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exit_error = exc
        self.log.append("uow_rollback" if exc else "uow_commit")
        return False


def _service(
    log, *, snapshot=SNAPSHOT, sync=None, firebase_uid="firebase-uid", user=None
):
    user = user or SimpleNamespace(id="db-user-id", is_active=True)
    uows: list[_Uow] = []

    def uow_factory():
        uow = _Uow(log, firebase_uid=firebase_uid, user=user)
        uows.append(uow)
        return uow

    async def read(uid):
        log.append(f"read:{uid}")
        if isinstance(snapshot, BaseException):
            raise snapshot
        return snapshot

    revenuecat = SimpleNamespace(get_verified_snapshot=AsyncMock(side_effect=read))

    async def default_sync(session, synced_user, **kwargs):
        log.append("sync")
        assert synced_user is user
        assert kwargs == {"snapshot": SNAPSHOT, "source": "refresh"}
        return SubscriptionSyncOutcome("applied", "denied", "granted", row_created=True)

    service = RevenueCatAccessRefreshService(
        lambda: revenuecat, uow_factory=uow_factory, sync=sync or default_sync
    )
    return service, revenuecat, uows


@pytest.fixture
def recorded(monkeypatch):
    calls = []
    monkeypatch.setattr(
        module,
        "record_snapshot_unavailable",
        lambda error, **kwargs: calls.append((error, kwargs)),
    )
    return calls


@pytest.mark.asyncio
async def test_reads_customer_outside_the_write_transaction_then_commits(recorded):
    log: list = []
    service, _, _ = _service(log)

    result = await service.refresh("db-user-id")

    assert result.result == "applied"
    assert log == [
        "uow_enter",
        "uow_commit",  # uid lookup, closed before the HTTP call
        "read:firebase-uid",
        "uow_enter",
        "sync",
        "uow_commit",
    ]
    assert recorded == []


@pytest.mark.asyncio
async def test_unreadable_customer_is_recorded_and_raised_without_a_write(recorded):
    log: list = []
    failure = RevenueCatSnapshotUnavailable(
        "rate limited", retry_after_seconds=7, reason="http_429"
    )
    service, _, _ = _service(log, snapshot=failure)

    with pytest.raises(RevenueCatSnapshotUnavailable) as raised:
        await service.refresh("db-user-id")

    assert raised.value is failure
    assert "sync" not in log
    assert log.count("uow_enter") == 1
    assert recorded == [(failure, {"source": "refresh", "user_id": "db-user-id"})]


@pytest.mark.asyncio
async def test_unsafe_snapshot_rolls_back_and_reports_not_applied(recorded):
    log: list = []
    refusal = RevenueCatSnapshotUnavailable(
        "absence cannot verify history", reason="absent_with_history"
    )

    async def refusing_sync(session, user, **kwargs):
        log.append("sync")
        raise refusal

    service, _, uows = _service(log, sync=refusing_sync)

    result = await service.refresh("db-user-id")

    assert (result.result, result.reason) == ("not_applied", "absent_with_history")
    assert uows[-1].exit_error is refusal
    assert log[-1] == "uow_rollback"
    assert recorded == [(refusal, {"source": "refresh", "user_id": "db-user-id"})]


@pytest.mark.asyncio
async def test_user_without_active_identity_is_not_read(recorded):
    log: list = []
    service, revenuecat, _ = _service(log, firebase_uid=None)

    result = await service.refresh("db-user-id")

    assert (result.result, result.reason) == ("not_applied", "owner_changed")
    revenuecat.get_verified_snapshot.assert_not_awaited()
    assert len(recorded) == 1


@pytest.mark.asyncio
async def test_user_deleted_between_read_and_write_is_not_applied(recorded):
    log: list = []
    service, _, _ = _service(
        log, user=SimpleNamespace(id="db-user-id", is_active=False)
    )

    result = await service.refresh("db-user-id")

    assert (result.result, result.reason) == ("not_applied", "owner_changed")
    assert "sync" not in log
    assert log[-1] == "uow_rollback"


@pytest.mark.asyncio
async def test_database_errors_propagate(monkeypatch):
    log: list = []
    captured = MagicMock()
    monkeypatch.setattr(module, "capture_exception", captured)

    async def broken_sync(session, user, **kwargs):
        raise RuntimeError("connection reset")

    service, _, _ = _service(log, sync=broken_sync)

    with pytest.raises(RuntimeError):
        await service.refresh("db-user-id")


@pytest.mark.asyncio
async def test_apply_failure_is_reported_once_under_its_fixed_issue(monkeypatch):
    """Reported inside the bus, before anything logs the exception, so the
    fingerprinted event is the one Sentry keeps; callers get an error that
    says so and must not report it again."""
    log: list = []
    captured = MagicMock()
    monkeypatch.setattr(module, "capture_exception", captured)
    failure = RuntimeError("connection reset")

    async def broken_sync(session, user, **kwargs):
        raise failure

    service, _, uows = _service(log, sync=broken_sync)

    with pytest.raises(SubscriptionAccessApplyError) as raised:
        await service.refresh("db-user-id")

    assert raised.value.__cause__ is failure
    assert uows[-1].exit_error is failure  # the unit of work rolled back
    captured.assert_called_once()
    assert captured.call_args.args[0] is failure
    assert captured.call_args.kwargs["fingerprint"] == [
        "subscription_access.sync_apply_error",
        "refresh",
    ]
    context = captured.call_args.kwargs["context"]
    assert (context["source"], context["user_id"]) == ("refresh", "db-user-id")


@pytest.mark.asyncio
async def test_a_failed_identity_lookup_is_not_an_apply_failure(monkeypatch):
    captured = MagicMock()
    monkeypatch.setattr(module, "capture_exception", captured)

    def broken_uow():
        raise RuntimeError("pool exhausted")

    service = RevenueCatAccessRefreshService(
        lambda: SimpleNamespace(), uow_factory=broken_uow
    )

    with pytest.raises(RuntimeError) as raised:
        await service.refresh("db-user-id")

    assert not isinstance(raised.value, SubscriptionAccessApplyError)
    captured.assert_not_called()


@pytest.mark.asyncio
async def test_a_support_resync_labels_its_read_and_sync_and_reports_the_outcome(
    recorded,
):
    log: list = []
    seen: dict = {}

    async def sync(session, user, **kwargs):
        seen.update(kwargs)
        return SubscriptionSyncOutcome(
            "absent",
            "unresolved",
            "denied",
            rows_revoked=2,
            markers_cleared=2,
            standard_missing=False,
        )

    service, _, _ = _service(log, sync=sync)

    result = await service.refresh("db-user-id", source="support_resync")

    assert seen == {"snapshot": SNAPSHOT, "source": "support_resync"}
    assert result == SubscriptionAccessRefresh(
        "absent", rows_revoked=2, row_created=False, markers_cleared=2
    )


@pytest.mark.asyncio
async def test_a_support_resync_failure_is_recorded_under_its_source(recorded):
    log: list = []
    failure = RevenueCatSnapshotUnavailable("down", reason="http_5xx")
    service, _, _ = _service(log, snapshot=failure)

    with pytest.raises(RevenueCatSnapshotUnavailable):
        await service.refresh("db-user-id", source="support_resync")

    assert recorded == [
        (failure, {"source": "support_resync", "user_id": "db-user-id"})
    ]


def _standard_customer(now):
    from tests.fixtures.revenuecat_samples import standard_annual_response

    body = standard_annual_response(
        request_date=now, expires_date=now + timedelta(days=200)
    )
    return RevenueCatSnapshot(
        subscriber=body["subscriber"], request_date=now, app_user_id="firebase-uid"
    )


@pytest.mark.asyncio
async def test_peek_reads_and_describes_the_customer_without_a_write(recorded):
    now = datetime.now(UTC).replace(microsecond=0)
    log: list = []
    service, revenuecat, uows = _service(
        log, snapshot=_standard_customer(now), sync=AsyncMock()
    )

    peek = await service.peek("db-user-id", source="support_lookup")

    assert revenuecat.get_verified_snapshot.await_args.args == ("firebase-uid",)
    # Only the identity lookup opened a unit of work; nothing was synced.
    assert log == ["uow_enter", "uow_commit", "read:firebase-uid"]
    assert len(uows) == 1
    assert (peek.read_at, peek.absent, peek.entitlements) == (now, False, ("standard",))
    assert peek.standard is not None and peek.standard.has_access is True
    assert recorded == []


@pytest.mark.asyncio
async def test_peek_never_reads_an_account_without_an_active_identity(recorded):
    log: list = []
    service, revenuecat, _ = _service(log, firebase_uid=None)

    with pytest.raises(RevenueCatSnapshotUnavailable) as raised:
        await service.peek("db-user-id", source="support_lookup")

    assert raised.value.reason == "owner_changed"
    revenuecat.get_verified_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_peek_failure_is_recorded_and_raised(recorded):
    log: list = []
    failure = RevenueCatSnapshotUnavailable("bad key", reason="http_4xx")
    service, _, _ = _service(log, snapshot=failure)

    with pytest.raises(RevenueCatSnapshotUnavailable) as raised:
        await service.peek("db-user-id", source="support_lookup")

    assert raised.value is failure
    assert recorded == [
        (failure, {"source": "support_lookup", "user_id": "db-user-id"})
    ]
