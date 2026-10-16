"""RevenueCat webhook with ``ent_ledger_write`` ON (NM-680, plan §6.1)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from tests.fixtures.fakes.fake_feature_flag_reader import StaticFeatureFlagReader

from src.api.routes.v1 import webhooks
from src.app.services.entitlement_inbox_service import (
    EventUserResolution,
    RecordedProviderEvent,
)
from src.domain.model.billing.constants import (
    FLAG_ENT_LEDGER_WRITE,
    FLAG_ENT_RECONCILE,
    ParkReason,
    SyncLane,
)

pytestmark = pytest.mark.asyncio

RAW_BODY = b'{"event":{"type":"RENEWAL"}}'
# The web-funnel redemption stage opens (and closes) its own UoW first.
REDEMPTION_UOW = ["enter", "exit"]


class _Inbox:
    """Spy for EntitlementInboxService that records call order."""

    def __init__(self, order, *, status="received", resolution=None):
        self.order = order
        self.status = status
        self.resolution = resolution or EventUserResolution(user_ids=("user-1",))
        self.recorded = []
        self.parked = []
        self.attached = []
        self.processed = []
        self.retried = []
        self.process_side_effect = None

    async def record(self, uow, payload, raw_body):
        self.order.append("record")
        self.recorded.append((payload, raw_body))
        return RecordedProviderEvent("row-1", inserted=True, status=self.status)

    async def resolve_users(self, uow, event):
        return self.resolution

    async def park(self, uow, event_id, reason):
        self.order.append("park")
        self.parked.append((event_id, reason))

    async def attach_user(self, uow, event_id, user_id):
        self.attached.append((event_id, user_id))

    async def process(self, event_id, *, lane):
        self.order.append("process")
        self.processed.append((event_id, lane))
        if self.process_side_effect is not None:
            return await self.process_side_effect()
        return "processed"

    async def schedule_retry(self, event_id, error):
        self.retried.append((event_id, error))
        return "retry"


@pytest.fixture
def order():
    return []


@pytest.fixture
def flags():
    return StaticFeatureFlagReader({FLAG_ENT_LEDGER_WRITE: True})


@pytest.fixture
def inbox(order):
    return _Inbox(order)


@pytest.fixture(autouse=True)
def wiring(monkeypatch, flags, inbox):
    monkeypatch.setattr(webhooks, "_get_feature_flag_reader", lambda: flags)
    monkeypatch.setattr(webhooks, "_get_entitlement_inbox_service", lambda: inbox)
    monkeypatch.setattr(
        webhooks, "record_web_funnel_redemption", AsyncMock(return_value=False)
    )
    with patch.multiple(
        "src.api.routes.v1.webhooks.settings",
        REVENUECAT_WEBHOOK_AUTH_HEADER="secret",
        REVENUECAT_WEBHOOK_SECRET="secret",
    ):
        yield


def _request(event):
    request = MagicMock()
    request.json = AsyncMock(return_value={"event": event})
    request.body = AsyncMock(return_value=RAW_BODY)
    return request


def _uow(order, *, user=None, inactive_user=None):
    """Legacy UoW mock: ``load_user_by_id`` and the legacy lookup share execute."""
    uow = MagicMock()

    async def enter():
        order.append("enter")
        return uow

    async def exit_(exc_type, exc, tb):
        order.append("exit:error" if exc_type else "exit")
        return False

    uow.__aenter__ = AsyncMock(side_effect=enter)
    uow.__aexit__ = AsyncMock(side_effect=exit_)
    result = MagicMock()
    result.scalars.return_value.first.return_value = user or inactive_user
    uow.session.execute = AsyncMock(return_value=result)
    uow.subscriptions.find_by_revenuecat_id = AsyncMock(return_value=None)
    return uow


async def _call(event, uow):
    with patch("src.api.routes.v1.webhooks.AsyncUnitOfWork", return_value=uow):
        return await webhooks.revenuecat_webhook(
            _request(event), authorization="secret"
        )


def _user(user_id="user-1", *, active=True):
    return MagicMock(id=user_id, firebase_uid="uid-1", is_active=active)


async def test_inbox_row_is_written_inside_the_legacy_uow_and_synced_after_commit(
    order, inbox
):
    uow = _uow(order, user=_user())
    with patch.object(webhooks, "handle_renewal", new=AsyncMock()) as renewal:
        result = await _call(
            {"id": "evt-1", "type": "RENEWAL", "app_user_id": "uid-1"}, uow
        )

    assert result == {"status": "success"}
    renewal.assert_awaited_once()
    # (redemption UoW) then: enter -> record (same transaction as the legacy
    # writes) -> commit -> inline sync
    assert order == [*REDEMPTION_UOW, "enter", "record", "exit", "process"]
    assert inbox.recorded[0][1] == RAW_BODY
    assert inbox.processed == [("row-1", SyncLane.WEBHOOK)]


async def test_user_found_only_by_the_legacy_lookup_is_kept_on_the_row(order, inbox):
    """The inline sync cannot repeat the legacy subscription-row fallback."""
    inbox.resolution = EventUserResolution(park_reason=ParkReason.USER_NOT_FOUND)
    uow = _uow(order, user=_user("user-legacy"))
    with patch.object(webhooks, "handle_renewal", new=AsyncMock()):
        result = await _call(
            {"id": "evt-1", "type": "RENEWAL", "app_user_id": "$RCAnonymousID:x"}, uow
        )

    assert result == {"status": "success"}
    assert inbox.attached == [("row-1", "user-legacy")]
    assert inbox.parked == []
    assert order[-1] == "process"


async def test_settled_duplicate_is_acknowledged_without_legacy_work(order, inbox):
    inbox.status = "processed"
    uow = _uow(order, user=_user())
    with (
        patch.object(webhooks, "handle_renewal", new=AsyncMock()) as renewal,
        patch.object(webhooks, "increment_metric") as metric,
    ):
        result = await _call(
            {"id": "evt-1", "type": "RENEWAL", "app_user_id": "uid-1"}, uow
        )

    assert result == {"status": "success", "duplicate": True}
    renewal.assert_not_awaited()
    assert "process" not in order
    metric.assert_any_call(
        "webhook.revenuecat.duplicate", attributes={"event_type": "RENEWAL"}
    )


async def test_unsettled_duplicate_is_reprocessed(order, inbox):
    inbox.status = "retry"
    uow = _uow(order, user=_user())
    with patch.object(webhooks, "handle_renewal", new=AsyncMock()) as renewal:
        result = await _call(
            {"id": "evt-1", "type": "RENEWAL", "app_user_id": "uid-1"}, uow
        )

    assert result == {"status": "success"}
    renewal.assert_awaited_once()
    assert order[-1] == "process"


async def test_unknown_user_keeps_legacy_404_while_reconcile_is_off(order, inbox):
    inbox.resolution = EventUserResolution(park_reason=ParkReason.USER_NOT_FOUND)
    uow = _uow(order)

    with pytest.raises(HTTPException) as exc_info:
        await _call({"id": "evt-1", "type": "RENEWAL", "app_user_id": "nobody"}, uow)

    assert exc_info.value.status_code == 404
    # The legacy UoW rolls back, taking the inbox row with it; no inline sync.
    assert order == [*REDEMPTION_UOW, "enter", "record", "exit:error"]
    assert inbox.parked == []


async def test_unknown_user_is_parked_with_200_while_reconcile_is_on(
    order, inbox, flags
):
    flags.set(FLAG_ENT_RECONCILE)
    inbox.resolution = EventUserResolution(park_reason=ParkReason.USER_NOT_FOUND)
    uow = _uow(order)
    with patch.object(webhooks, "increment_metric") as metric:
        result = await _call(
            {"id": "evt-1", "type": "RENEWAL", "app_user_id": "nobody"}, uow
        )

    assert result == {"status": "parked", "reason": "user_not_found"}
    assert inbox.parked == [("row-1", ParkReason.USER_NOT_FOUND)]
    assert order == [*REDEMPTION_UOW, "enter", "record", "park", "exit"]
    metric.assert_any_call(
        "webhook.revenuecat.unmatched",
        attributes={"event_type": "RENEWAL", "reason": ParkReason.USER_NOT_FOUND},
    )


async def test_purchase_redeemed_for_unknown_user_follows_the_same_rule(
    order, inbox, flags
):
    inbox.resolution = EventUserResolution(park_reason=ParkReason.USER_NOT_FOUND)
    event = {"id": "evt-1", "type": "PURCHASE_REDEEMED", "redeemed_by": ["nobody"]}
    with pytest.raises(HTTPException):
        await _call(event, _uow(order))

    flags.set(FLAG_ENT_RECONCILE)
    assert (await _call(event, _uow(order)))["status"] == "parked"


async def test_deleted_user_is_never_written_and_is_acknowledged(order, inbox):
    inbox.resolution = EventUserResolution(park_reason=ParkReason.USER_NOT_FOUND)
    # The legacy lookup still finds the soft-deleted account.
    uow = _uow(order, inactive_user=_user(active=False))
    with patch.object(webhooks, "handle_renewal", new=AsyncMock()) as renewal:
        result = await _call(
            {"id": "evt-1", "type": "RENEWAL", "app_user_id": "uid-1"}, uow
        )

    assert result == {"status": "ignored", "reason": "deleted_user"}
    renewal.assert_not_awaited()
    assert inbox.parked == [("row-1", ParkReason.DELETED_USER)]
    assert "process" not in order


async def test_userless_lifecycle_event_keeps_legacy_body_and_is_parked(order, inbox):
    inbox.resolution = EventUserResolution(park_reason=ParkReason.USER_NOT_FOUND)
    result = await _call(
        {"id": "evt-1", "type": "CANCELLATION", "app_user_id": "$RCAnonymousID:x"},
        _uow(order),
    )

    assert result == {"status": "ignored", "reason": "user_not_found"}
    assert inbox.parked == [("row-1", ParkReason.USER_NOT_FOUND)]


async def test_identity_resolution_wins_over_the_legacy_lookup(order, inbox):
    inbox.resolution = EventUserResolution(user_ids=("user-2",))
    uow = _uow(order, user=_user("user-2"))
    with patch.object(webhooks, "handle_renewal", new=AsyncMock()) as renewal:
        await _call({"id": "evt-1", "type": "RENEWAL", "app_user_id": "alias"}, uow)

    statement = uow.session.execute.await_args_list[0].args[0]
    assert "user-2" in statement.compile().params.values()
    assert renewal.await_args.args[1].id == "user-2"


async def test_transfer_runs_legacy_transfer_then_inline_sync(order, inbox):
    uow = _uow(order, user=_user())
    with (
        patch.object(webhooks, "handle_transfer", new=AsyncMock()) as transfer,
        patch.object(
            webhooks, "sync_redeemed_target_subscription", new=AsyncMock()
        ) as redeemed,
    ):
        result = await _call(
            {
                "id": "t-1",
                "type": "TRANSFER",
                "transferred_from": ["uid-0"],
                "transferred_to": ["uid-1"],
            },
            uow,
        )

    assert result == {"status": "success"}
    transfer.assert_awaited_once()
    redeemed.assert_awaited_once()
    assert order == [*REDEMPTION_UOW, "enter", "record", "exit", "process"]


async def test_inline_sync_failure_never_changes_the_response(order, inbox):
    async def boom():
        raise RuntimeError("provider exploded")

    inbox.process_side_effect = boom
    with patch.object(webhooks, "handle_renewal", new=AsyncMock()):
        result = await _call(
            {"id": "evt-1", "type": "RENEWAL", "app_user_id": "uid-1"},
            _uow(order, user=_user()),
        )

    assert result == {"status": "success"}


async def test_inline_sync_over_budget_is_left_for_retry(order, inbox, monkeypatch):
    monkeypatch.setattr(webhooks, "INLINE_SYNC_BUDGET_SECONDS", 0.01)

    async def slow():
        await asyncio.sleep(1)

    inbox.process_side_effect = slow
    with patch.object(webhooks, "handle_renewal", new=AsyncMock()):
        result = await _call(
            {"id": "evt-1", "type": "RENEWAL", "app_user_id": "uid-1"},
            _uow(order, user=_user()),
        )

    assert result == {"status": "success"}
    assert inbox.retried == [("row-1", "inline_sync_timeout")]


async def test_raw_body_falls_back_to_canonical_json(order, inbox):
    request = _request({"id": "evt-1", "type": "RENEWAL", "app_user_id": "uid-1"})
    request.body = AsyncMock(side_effect=RuntimeError("stream consumed"))
    with (
        patch(
            "src.api.routes.v1.webhooks.AsyncUnitOfWork",
            return_value=_uow(order, user=_user()),
        ),
        patch.object(webhooks, "handle_renewal", new=AsyncMock()),
    ):
        await webhooks.revenuecat_webhook(request, authorization="secret")

    assert inbox.recorded[0][1].startswith(b'{"event": {')
