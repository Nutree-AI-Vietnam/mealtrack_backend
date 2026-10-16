"""EntitlementInboxService with in-memory fakes (NM-680)."""

import hashlib
import json
from dataclasses import replace
from datetime import timedelta

import pytest
from tests.fixtures.fakes.fake_billing_provider import (
    NOW,
    FakeBillingProvider,
    FakeBudget,
    FakeClock,
    make_sub,
    make_user,
    snapshot,
)
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.app.services.entitlement_inbox_service import (
    INLINE_SYNC_GRACE,
    OUTCOME_DEAD,
    OUTCOME_IGNORED,
    OUTCOME_LEASED,
    OUTCOME_MISSING,
    OUTCOME_PROCESSED,
    OUTCOME_RETRY,
    OUTCOME_SETTLED,
    OUTCOME_UNMATCHED,
    EntitlementInboxService,
    candidate_customer_ids,
    retry_delay,
    revenuecat_provider_event_id,
    strip_subscriber_attributes,
)
from src.app.services.entitlement_sync_service import EntitlementSyncService
from src.domain.exceptions.billing_exceptions import ProviderUnavailable
from src.domain.model.billing.constants import (
    ENTITLEMENT_STANDARD,
    IdentityKind,
    ParkReason,
    ProviderEventStatus,
    SyncLane,
)
from src.domain.ports.entitlement_ledger_repository_port import IdentityLinkOutcome

pytestmark = pytest.mark.asyncio

U1, U2 = "user-1", "user-2"
UID1, UID2 = "uid-1", "uid-2"


@pytest.fixture
def uow():
    store = FakeUnitOfWork()
    store.users.users[U1] = make_user(U1, UID1)
    store.users.users[U2] = make_user(U2, UID2)
    return store


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def provider():
    fake = FakeBillingProvider()
    fake.responses[UID1] = snapshot(UID1, make_sub(UID1))
    fake.responses[UID2] = snapshot(UID2)
    return fake


@pytest.fixture
def sync(uow, provider, clock):
    return EntitlementSyncService(
        uow_factory=lambda: uow, provider=provider, budget=FakeBudget(), clock=clock
    )


@pytest.fixture
def inbox(uow, sync, clock):
    counter = iter(range(1, 1000))
    return EntitlementInboxService(
        uow_factory=lambda: uow,
        sync_service=sync,
        clock=clock,
        id_factory=lambda: f"row-{next(counter)}",
    )


def _payload(event_type="RENEWAL", app_user_id=UID1, **event):
    return {
        "api_version": "1.0",
        "event": {"type": event_type, "app_user_id": app_user_id, **event},
    }


async def _record(inbox, uow, payload, raw=b"{}"):
    async with uow:
        return await inbox.record(uow, payload, raw)


# ---- pure helpers ----


def test_provider_event_id_prefers_rc_event_id():
    assert revenuecat_provider_event_id({"id": " evt-1 "}, b"body") == "evt-1"


def test_provider_event_id_hashes_raw_body_when_missing():
    body = b'{"event": {"type": "RENEWAL"}}'
    expected = "sha256:" + hashlib.sha256(body).hexdigest()
    assert revenuecat_provider_event_id({}, body) == expected
    assert revenuecat_provider_event_id({"id": ""}, body) == expected


def test_subscriber_attributes_are_stripped_without_mutating_input():
    payload = {
        "event": {
            "type": "RENEWAL",
            "subscriber_attributes": {"$ip": {"value": "1.2.3.4"}},
        },
        "subscriber_attributes": {"$idfa": {"value": "x"}},
    }
    stored = strip_subscriber_attributes(payload)

    assert "subscriber_attributes" not in stored
    assert "subscriber_attributes" not in stored["event"]
    assert stored["event"]["type"] == "RENEWAL"
    assert "subscriber_attributes" in payload["event"]


def test_candidate_ids_follow_legacy_order_without_duplicates():
    event = {
        "app_user_id": "a",
        "original_app_user_id": "b",
        "aliases": ["a", "c"],
        "transferred_to": ["d"],
        "transferred_from": ["e", None],
    }
    assert candidate_customer_ids(event) == ["a", "b", "c", "d", "e"]


def test_retry_delay_is_exponential_and_capped():
    assert retry_delay(0) == timedelta(seconds=30)
    assert retry_delay(3) == timedelta(seconds=240)
    assert retry_delay(50) == timedelta(hours=6)


# ---- recording ----


async def test_record_inserts_received_row_with_stripped_raw(inbox, uow):
    payload = _payload(
        id="evt-1",
        environment="SANDBOX",
        event_timestamp_ms=1_760_515_200_000,
        subscriber_attributes={"$email": {"value": "a@b.c"}},
    )
    recorded = await _record(inbox, uow, payload)

    assert recorded.inserted and not recorded.settled
    row = uow.billing_events.rows[recorded.event_id]
    assert row.provider_event_id == "evt-1"
    assert row.status == ProviderEventStatus.RECEIVED
    assert row.event_type == "RENEWAL"
    assert row.environment == "SANDBOX"
    assert row.app_user_id == UID1
    assert row.event_ts is not None
    assert "subscriber_attributes" not in json.dumps(row.raw)


async def test_duplicate_delivery_is_deduped_on_provider_event_id(inbox, uow):
    first = await _record(inbox, uow, _payload(id="evt-1"))
    second = await _record(inbox, uow, _payload(id="evt-1"))

    assert first.inserted and not second.inserted
    assert second.event_id == first.event_id
    assert len(uow.billing_events.rows) == 1
    assert not second.settled  # still "received": it will be reprocessed


async def test_duplicate_of_processed_row_reads_as_settled(inbox, uow):
    first = await _record(inbox, uow, _payload(id="evt-1"))
    await uow.billing_events.mark_processed(first.event_id, processed_at=NOW)

    assert (await _record(inbox, uow, _payload(id="evt-1"))).settled


async def test_events_without_id_dedupe_on_body_hash(inbox, uow):
    body = b'{"event":{"type":"RENEWAL"}}'
    await _record(inbox, uow, _payload(), raw=body)
    await _record(inbox, uow, _payload(), raw=body)
    await _record(inbox, uow, _payload(), raw=body + b" ")

    assert len(uow.billing_events.rows) == 2


# ---- user resolution ----


async def test_resolution_prefers_billing_identity(inbox, uow):
    await uow.billing_identities.add_alias(
        U2, "revenuecat", "$RCAnonymousID:x", IdentityKind.ANONYMOUS, seen_at=NOW
    )
    resolution = await inbox.resolve_users(
        uow, {"type": "RENEWAL", "app_user_id": "$RCAnonymousID:x"}
    )
    assert resolution.user_ids == (U2,)


async def test_resolution_falls_back_to_firebase_uid_and_internal_id(inbox, uow):
    by_uid = await inbox.resolve_users(uow, {"app_user_id": UID1})
    uow.users.users["0b3c3a7e-5b1d-4d55-9a52-0d8c4f1b2a11"] = make_user(
        "0b3c3a7e-5b1d-4d55-9a52-0d8c4f1b2a11", "uid-3"
    )
    by_id = await inbox.resolve_users(
        uow, {"app_user_id": "0B3C3A7E-5B1D-4D55-9A52-0D8C4F1B2A11"}
    )
    assert by_uid.user_ids == (U1,)
    assert by_id.user_ids == ("0b3c3a7e-5b1d-4d55-9a52-0d8c4f1b2a11",)


async def test_resolution_ignores_inactive_users(inbox, uow):
    uow.users.users[U1].is_active = False
    resolution = await inbox.resolve_users(uow, {"app_user_id": UID1})

    assert resolution.user_ids == ()
    assert resolution.park_reason == ParkReason.DELETED_USER


async def test_resolution_ignores_retired_identities(inbox, uow):
    await uow.billing_identities.add_alias(
        U2, "revenuecat", "lead-1", IdentityKind.LEGACY_LEAD, seen_at=NOW
    )
    await uow.billing_identities.retire_for_user(U2, retired_at=NOW)

    resolution = await inbox.resolve_users(uow, {"app_user_id": "lead-1"})

    assert resolution.user_ids == ()
    assert resolution.park_reason == ParkReason.DELETED_USER


async def test_unknown_ids_park_as_user_not_found(inbox, uow):
    resolution = await inbox.resolve_users(uow, {"app_user_id": "nobody"})
    assert resolution.park_reason == ParkReason.USER_NOT_FOUND


async def test_transfer_resolves_both_sides(inbox, uow):
    resolution = await inbox.resolve_users(
        uow,
        {
            "type": "TRANSFER",
            "transferred_from": [UID1],
            "transferred_to": [UID2],
        },
    )
    assert resolution.user_ids == (U2, U1)
    assert resolution.primary_user_id == U2


# ---- processing ----


async def test_process_syncs_user_and_marks_processed(inbox, uow, provider):
    recorded = await _record(inbox, uow, _payload(id="evt-1"))

    outcome = await inbox.process(recorded.event_id, lane=SyncLane.WEBHOOK)

    row = uow.billing_events.rows[recorded.event_id]
    assert outcome == OUTCOME_PROCESSED
    assert row.status == ProviderEventStatus.PROCESSED
    assert row.user_id == U1
    assert provider.calls == [UID1]
    projection = await uow.user_entitlements.get(U1, ENTITLEMENT_STANDARD)
    assert projection is not None and projection.has_access


async def test_process_transfer_resyncs_both_users(inbox, uow, provider, sync):
    await sync.sync_user(U1, SyncLane.WEBHOOK, "seed")
    assert (await uow.user_entitlements.get(U1)).has_access
    # The subscription moved: only the target's customer lists it now.
    provider.responses[UID1] = snapshot(UID1, fetched_at=NOW + timedelta(seconds=1))
    provider.responses[UID2] = snapshot(
        UID2, make_sub(UID1), fetched_at=NOW + timedelta(seconds=1)
    )
    provider.calls.clear()
    recorded = await _record(
        inbox,
        uow,
        {
            "event": {
                "id": "t-1",
                "type": "TRANSFER",
                "transferred_from": [UID1],
                "transferred_to": [UID2],
            }
        },
    )

    assert await inbox.process(recorded.event_id, lane=SyncLane.WEBHOOK) == (
        OUTCOME_PROCESSED
    )
    assert sorted(provider.calls) == [UID1, UID2]
    assert (await uow.user_entitlements.get(U2)).has_access
    assert not (await uow.user_entitlements.get(U1)).has_access


async def test_provider_error_schedules_retry_with_backoff(inbox, uow, provider):
    provider.responses[UID1] = ProviderUnavailable("down")
    recorded = await _record(inbox, uow, _payload(id="evt-1"))

    outcome = await inbox.process(recorded.event_id, lane=SyncLane.WEBHOOK)

    row = uow.billing_events.rows[recorded.event_id]
    assert outcome == OUTCOME_RETRY
    assert row.status == ProviderEventStatus.RETRY
    assert row.attempts == 1
    assert row.last_error == "provider_unavailable"
    assert row.next_attempt_at == NOW + timedelta(seconds=30)


async def test_row_goes_dead_after_max_attempts(inbox, uow, provider):
    provider.responses[UID1] = ProviderUnavailable("down")
    recorded = await _record(inbox, uow, _payload(id="evt-1"))
    row = uow.billing_events.rows[recorded.event_id]
    uow.billing_events.rows[row.id] = replace(row, attempts=9)

    outcome = await inbox.process_claimed(
        uow.billing_events.rows[row.id], lane=SyncLane.RECONCILE
    )

    assert outcome == OUTCOME_DEAD
    assert uow.billing_events.rows[row.id].status == ProviderEventStatus.DEAD
    assert uow.billing_events.rows[row.id].attempts == 10


async def test_unexpected_error_is_retried_not_raised(inbox, uow, sync, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(sync, "sync_user", boom)
    recorded = await _record(inbox, uow, _payload(id="evt-1"))

    assert await inbox.process(recorded.event_id, lane=SyncLane.WEBHOOK) == (
        OUTCOME_RETRY
    )
    assert "RuntimeError" in uow.billing_events.rows[recorded.event_id].last_error


async def test_unknown_user_is_parked(inbox, uow, provider):
    recorded = await _record(inbox, uow, _payload(id="evt-1", app_user_id="nobody"))

    assert await inbox.process(recorded.event_id, lane=SyncLane.WEBHOOK) == (
        OUTCOME_UNMATCHED
    )
    row = uow.billing_events.rows[recorded.event_id]
    assert row.status == ProviderEventStatus.UNMATCHED
    assert row.park_reason == ParkReason.USER_NOT_FOUND
    assert provider.calls == []


async def test_test_events_are_ignored(inbox, uow, provider):
    recorded = await _record(inbox, uow, _payload("TEST", id="evt-1"))

    assert await inbox.process(recorded.event_id, lane=SyncLane.WEBHOOK) == (
        OUTCOME_IGNORED
    )
    assert uow.billing_events.rows[recorded.event_id].status == "ignored"
    assert provider.calls == []


async def test_settled_and_missing_rows_are_not_reprocessed(inbox, uow, provider):
    recorded = await _record(inbox, uow, _payload(id="evt-1"))
    await uow.billing_events.mark_processed(recorded.event_id, processed_at=NOW)

    assert await inbox.process(recorded.event_id, lane="webhook") == OUTCOME_SETTLED
    assert await inbox.process("nope", lane="webhook") == OUTCOME_MISSING
    assert provider.calls == []


async def test_schedule_retry_after_inline_timeout(inbox, uow):
    recorded = await _record(inbox, uow, _payload(id="evt-1"))

    assert await inbox.schedule_retry(recorded.event_id, "inline_sync_timeout") == (
        OUTCOME_RETRY
    )
    row = uow.billing_events.rows[recorded.event_id]
    assert row.status == ProviderEventStatus.RETRY
    assert row.last_error == "inline_sync_timeout"


async def test_user_attached_by_the_route_is_synced_when_ids_do_not_resolve(
    inbox, uow, provider
):
    """The route matched the user via a legacy subscription row only."""
    anonymous = "$RCAnonymousID:legacy"
    async with uow:
        recorded = await inbox.record(
            uow, _payload(id="evt-1", app_user_id=anonymous), b"{}"
        )
        await inbox.attach_user(uow, recorded.event_id, U1)

    assert await inbox.process(recorded.event_id, lane=SyncLane.WEBHOOK) == (
        OUTCOME_PROCESSED
    )
    assert provider.calls == [UID1]
    row = uow.billing_events.rows[recorded.event_id]
    assert row.status == ProviderEventStatus.PROCESSED and row.user_id == U1
    assert (await uow.user_entitlements.get(U1)).has_access


async def test_parked_row_redelivered_for_a_matched_user_is_requeued_and_synced(
    inbox, uow, provider
):
    """First delivery parked (user unknown); the redelivery matches the user."""
    payload = _payload(id="evt-late", app_user_id="uid-new")
    recorded = await _record(inbox, uow, payload)
    async with uow:
        await inbox.park(uow, recorded.event_id, ParkReason.USER_NOT_FOUND)
    uow.users.users["user-new"] = make_user("user-new", "uid-new")
    provider.responses["uid-new"] = snapshot("uid-new", make_sub("uid-new"))

    async with uow:
        again = await inbox.record(uow, payload, b"{}")
        assert again.status == ProviderEventStatus.UNMATCHED and not again.settled
        await inbox.attach_user(uow, again.event_id, "user-new")

    row = uow.billing_events.rows[recorded.event_id]
    assert (row.status, row.park_reason) == (ProviderEventStatus.RECEIVED, None)
    # Due only after the inline grace, like a new row: the inline sync goes first.
    assert row.next_attempt_at == NOW + INLINE_SYNC_GRACE
    assert await inbox.process(recorded.event_id, lane=SyncLane.WEBHOOK) == (
        OUTCOME_PROCESSED
    )
    assert provider.calls == ["uid-new"]
    assert (await uow.user_entitlements.get("user-new")).has_access


async def test_attach_user_leaves_processed_rows_alone(inbox, uow, provider):
    recorded = await _record(inbox, uow, _payload(id="evt-1"))
    await inbox.process(recorded.event_id, lane=SyncLane.WEBHOOK)

    async with uow:
        await inbox.attach_user(uow, recorded.event_id, U1)

    assert uow.billing_events.rows[recorded.event_id].status == (
        ProviderEventStatus.PROCESSED
    )


async def test_attached_user_deleted_since_is_parked_as_deleted(inbox, uow, provider):
    async with uow:
        recorded = await inbox.record(
            uow, _payload(id="evt-1", app_user_id="$RCAnonymousID:x"), b"{}"
        )
        await inbox.attach_user(uow, recorded.event_id, U1)
    uow.users.users[U1] = make_user(U1, UID1, active=False)

    assert await inbox.process(recorded.event_id, lane=SyncLane.WEBHOOK) == (
        OUTCOME_UNMATCHED
    )
    row = uow.billing_events.rows[recorded.event_id]
    assert row.park_reason == ParkReason.DELETED_USER
    assert row.raw is None  # a deleted account's body is never kept (D11)
    assert provider.calls == []


async def test_new_rows_wait_for_the_inline_sync_before_the_worker(
    inbox, uow, provider, clock
):
    recorded = await _record(inbox, uow, _payload(id="evt-1"))
    lease = {"lease_owner": "worker", "lease_expires_at": NOW + timedelta(minutes=10)}

    # A worker poll during the inline sync finds nothing due ...
    assert await uow.billing_events.claim_due(now=NOW, limit=10, **lease) == []
    assert await inbox.process(recorded.event_id, lane=SyncLane.WEBHOOK) == (
        OUTCOME_PROCESSED
    )
    # ... and never picks the row up once the inline sync settled it.
    assert (
        await uow.billing_events.claim_due(
            now=NOW + timedelta(minutes=5), limit=10, **lease
        )
        == []
    )
    assert provider.calls == [UID1]


async def test_unfinished_rows_become_due_after_the_inline_grace(inbox, uow, provider):
    recorded = await _record(inbox, uow, _payload(id="evt-1"))
    lease = {"lease_owner": "worker", "lease_expires_at": NOW + timedelta(minutes=10)}

    claimed = await uow.billing_events.claim_due(
        now=NOW + timedelta(seconds=31), limit=10, **lease
    )

    assert [row.id for row in claimed] == [recorded.event_id]
    # A redelivery while the worker holds the lease does not fetch again.
    assert await inbox.process(recorded.event_id, lane=SyncLane.WEBHOOK) == (
        OUTCOME_LEASED
    )
    assert provider.calls == []


async def test_purged_raw_falls_back_to_stored_app_user_id(inbox, uow):
    recorded = await _record(inbox, uow, _payload(id="evt-1"))
    row = uow.billing_events.rows[recorded.event_id]
    uow.billing_events.rows[row.id] = replace(row, raw=None)

    assert await inbox.process(row.id, lane="webhook") == OUTCOME_PROCESSED


# ---- identities and replays ----


async def test_link_primary_identity(inbox, uow):
    assert await inbox.link_primary_identity(U1, UID1) == IdentityLinkOutcome.INSERTED
    assert (await uow.billing_identities.get_primary(U1)).provider_customer_id == UID1
    assert await inbox.link_primary_identity(U2, UID1) == IdentityLinkOutcome.CONFLICT


async def test_replay_unmatched_for_user_requeues_parked_rows(inbox, uow):
    mine = await _record(inbox, uow, _payload(id="evt-1", app_user_id=UID1))
    other = await _record(inbox, uow, _payload(id="evt-2", app_user_id="nobody"))
    await uow.billing_events.park(mine.event_id, reason="user_not_found")
    await uow.billing_events.park(other.event_id, reason="user_not_found")

    assert await inbox.replay_unmatched_for_user(U1) == 1

    row = uow.billing_events.rows[mine.event_id]
    assert row.status == ProviderEventStatus.RECEIVED
    assert row.user_id == U1
    assert uow.billing_events.rows[other.event_id].status == "unmatched"


async def test_sweep_requeues_rows_that_now_resolve_and_pages(inbox, uow, clock):
    ids = []
    for index, customer in enumerate(["nobody-1", UID1, "nobody-2"]):
        clock.now = NOW + timedelta(seconds=index)
        recorded = await _record(
            inbox, uow, _payload(id=f"evt-{index}", app_user_id=customer)
        )
        await uow.billing_events.park(recorded.event_id, reason="user_not_found")
        ids.append(recorded.event_id)

    requeued, cursor = await inbox.sweep_unmatched(limit=2)
    assert requeued == 1
    assert cursor is not None  # a full page: continue from here
    requeued, cursor = await inbox.sweep_unmatched(limit=2, after=cursor)
    assert requeued == 0
    assert cursor is None  # reached the newest parked row
    assert uow.billing_events.rows[ids[1]].status == ProviderEventStatus.RECEIVED
