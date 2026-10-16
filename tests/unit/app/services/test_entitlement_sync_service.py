"""EntitlementSyncService with in-memory fakes (NM-680)."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

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

from src.app.services import entitlement_sync_service as sync_module
from src.app.services.entitlement_sync_service import (
    USER_NOT_FOUND,
    EntitlementAccessConfig,
    EntitlementSyncService,
)
from src.domain.exceptions.billing_exceptions import (
    InvalidProviderPayload,
    ProviderRateLimited,
    ProviderUnavailable,
)
from src.domain.model.billing.constants import (
    ENTITLEMENT_STANDARD,
    IdentityKind,
    StoreSubscriptionState,
    SyncLane,
)
from src.domain.ports.entitlement_ledger_repository_port import IdentityLinkOutcome

pytestmark = pytest.mark.asyncio

U1, U2 = "user-1", "user-2"
UID1, UID2 = "uid-1", "uid-2"


class TrackingUnitOfWork(FakeUnitOfWork):
    """One shared fake store; counts open transactions."""

    def __init__(self):
        super().__init__()
        self.open_transactions = 0
        self.entered = 0

    async def __aenter__(self):
        self.open_transactions += 1
        self.entered += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.open_transactions -= 1
        await super().__aexit__(exc_type, exc, tb)


@pytest.fixture
def uow():
    store = TrackingUnitOfWork()
    store.users.users[U1] = make_user(U1, UID1)
    store.users.users[U2] = make_user(U2, UID2)
    return store


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def provider(uow):
    fake = FakeBillingProvider()

    def assert_no_transaction(_customer_id):
        assert uow.open_transactions == 0, "provider fetched inside a transaction"

    fake.on_fetch = assert_no_transaction
    return fake


@pytest.fixture
def budget():
    return FakeBudget()


def _service(uow, provider, budget, clock, **kwargs):
    return EntitlementSyncService(
        uow_factory=lambda: uow,
        provider=provider,
        budget=budget,
        clock=clock,
        **kwargs,
    )


@pytest.fixture
def service(uow, provider, budget, clock):
    return _service(uow, provider, budget, clock)


async def test_first_sync_writes_rows_identity_and_projection(
    service, uow, provider, budget
):
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))

    result = await service.sync_user(U1, SyncLane.WEBHOOK, "test")

    assert result.applied and not result.stale and result.sync_error is None
    projection = result.projection
    assert projection.has_access is True
    assert projection.state == "active"
    assert projection.version == 1
    assert projection.last_provider_sync_at == NOW
    assert projection.primary_ref == f"rc:{UID1}:nutree_monthly"
    rows = await uow.store_subscriptions.list_for_user(U1)
    assert [(r.subscription_key, r.state) for r in rows] == [
        (f"rc:{UID1}:nutree_monthly", "active")
    ]
    primary = await uow.billing_identities.get_primary(U1)
    assert primary.provider_customer_id == UID1
    assert budget.lanes == ["webhook"]
    assert provider.calls == [UID1]
    assert await uow.user_entitlements.get(U1) == projection
    assert result.provider_reports_active is True


async def test_resync_bumps_version_and_keeps_one_row(service, uow, provider, clock):
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))
    await service.sync_user(U1, SyncLane.WEBHOOK, "first")
    later = clock.advance(minutes=5)
    provider.responses[UID1] = snapshot(
        UID1, make_sub(UID1, will_renew=False), fetched_at=later
    )

    result = await service.sync_user(U1, SyncLane.LOGIN_SYNC, "second")

    assert result.projection.version == 2
    assert result.projection.state == "cancelled_active"
    rows = await uow.store_subscriptions.list_for_user(U1)
    assert len(rows) == 1
    assert rows[0].version == 2
    assert rows[0].will_renew is False


@pytest.mark.parametrize("delta_seconds", [0, -30])
async def test_stale_snapshot_is_discarded(
    service, uow, provider, clock, delta_seconds
):
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))
    first = await service.sync_user(U1, SyncLane.WEBHOOK, "fresh")
    clock.advance(minutes=1)
    # Fetched no later than the snapshot already applied: says the sub expired.
    provider.responses[UID1] = snapshot(
        UID1,
        make_sub(UID1, state="expired", expires_at=NOW - timedelta(days=1)),
        fetched_at=NOW + timedelta(seconds=delta_seconds),
    )

    result = await service.sync_user(U1, SyncLane.WEBHOOK, "late")

    assert result.discarded is True
    assert result.applied is False
    assert result.projection == first.projection
    rows = await uow.store_subscriptions.list_for_user(U1)
    assert rows[0].state == "active" and rows[0].version == 1
    assert (await uow.user_entitlements.get(U1)).version == 1


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ProviderUnavailable("down"), "provider_unavailable"),
        (ProviderRateLimited("slow", retry_after_seconds=2), "provider_rate_limited"),
        (InvalidProviderPayload("bad body"), "provider_invalid_payload"),
    ],
)
async def test_provider_error_never_reduces_access(
    service, uow, provider, clock, error, code
):
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))
    before = (await service.sync_user(U1, SyncLane.WEBHOOK, "ok")).projection
    rows_before = await uow.store_subscriptions.list_for_user(U1)
    clock.advance(minutes=1)
    provider.responses[UID1] = error

    with patch.object(sync_module, "increment_metric") as metric:
        result = await service.sync_user(U1, SyncLane.PURCHASE_SYNC, "hint")

    assert result.stale is True
    assert result.sync_error == code
    assert result.projection == before
    assert result.projection.has_access is True
    assert await uow.user_entitlements.get(U1) == before
    assert await uow.store_subscriptions.list_for_user(U1) == rows_before
    metric.assert_any_call(
        "entitlement.sync.provider_error",
        attributes={"code": code, "lane": "purchase_sync", "customer": "primary"},
    )


async def test_provider_error_for_never_synced_user_returns_no_projection(
    service, provider
):
    provider.responses[UID1] = ProviderUnavailable("down")

    result = await service.sync_user(U1, SyncLane.LOGIN_SYNC, "login")

    assert result.stale is True
    assert result.projections == {}


async def test_hinted_sync_failure_records_hint_without_touching_access(
    service, uow, provider, clock
):
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))
    before = (await service.sync_user(U1, SyncLane.WEBHOOK, "ok")).projection
    hint_at = clock.advance(minutes=2)
    provider.responses[UID1] = ProviderUnavailable("down")

    result = await service.sync_user(
        U1, SyncLane.PURCHASE_SYNC, "purchase", purchase_hint=True
    )

    stored = await uow.user_entitlements.get(U1)
    assert stored.last_purchase_hint_at == hint_at
    assert stored.has_access is True and stored.version == before.version
    assert result.projection.last_purchase_hint_at == hint_at


async def test_hinted_sync_success_sets_hint(service, uow, provider, clock):
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))

    result = await service.sync_user(
        U1, SyncLane.PURCHASE_SYNC, "purchase", purchase_hint=True
    )

    assert result.projection.last_purchase_hint_at == NOW
    assert (await uow.user_entitlements.get(U1)).last_purchase_hint_at == NOW


async def test_snapshot_replace_marks_missing_rows_absent(
    service, uow, provider, clock
):
    provider.responses[UID1] = snapshot(
        UID1, make_sub(UID1, "nutree_monthly"), make_sub(UID1, "nutree_yearly")
    )
    await service.sync_user(U1, SyncLane.WEBHOOK, "both")
    later = clock.advance(minutes=1)
    provider.responses[UID1] = snapshot(
        UID1, make_sub(UID1, "nutree_monthly"), fetched_at=later
    )

    result = await service.sync_user(U1, SyncLane.WEBHOOK, "one gone")

    rows = {r.product_id: r for r in await uow.store_subscriptions.list_for_user(U1)}
    assert rows["nutree_yearly"].state == StoreSubscriptionState.ABSENT
    assert rows["nutree_yearly"].absent_since == later
    assert rows["nutree_monthly"].state == "active"
    assert result.projection.has_access is True


async def test_transfer_away_removes_access(service, uow, provider, clock):
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))
    await service.sync_user(U1, SyncLane.WEBHOOK, "bought")
    later = clock.advance(minutes=1)
    provider.responses[UID1] = snapshot(UID1, fetched_at=later)

    result = await service.sync_user(U1, SyncLane.WEBHOOK, "transfer")

    assert result.projection.has_access is False
    assert result.projection.state == "expired"
    (row,) = await uow.store_subscriptions.list_for_user(U1)
    assert row.state == "absent"


async def test_absent_rows_are_not_rewritten(service, uow, provider, clock):
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))
    await service.sync_user(U1, SyncLane.WEBHOOK, "bought")
    provider.responses[UID1] = snapshot(UID1, fetched_at=clock.advance(minutes=1))
    await service.sync_user(U1, SyncLane.WEBHOOK, "gone")
    (absent,) = await uow.store_subscriptions.list_for_user(U1)
    provider.responses[UID1] = snapshot(UID1, fetched_at=clock.advance(minutes=1))

    await service.sync_user(U1, SyncLane.WEBHOOK, "still gone")

    (row,) = await uow.store_subscriptions.list_for_user(U1)
    assert row == absent


async def test_key_conflict_moves_row_and_recomputes_previous_owner(
    service, uow, provider, clock
):
    # Both Firebase accounts resolve to one RevenueCat customer ("shared").
    shared_sub = make_sub("shared")
    provider.responses[UID1] = snapshot("shared", shared_sub, requested=UID1)
    first = await service.sync_user(U1, SyncLane.WEBHOOK, "u1")
    assert first.projection.has_access is True
    later = clock.advance(minutes=1)
    provider.responses[UID2] = snapshot(
        "shared", shared_sub, requested=UID2, fetched_at=later
    )
    uow.user_entitlements.lock_calls.clear()

    result = await service.sync_user(U2, SyncLane.WEBHOOK, "u2")

    assert result.projection.has_access is True
    (row,) = uow.store_subscriptions.rows.values()
    assert row.user_id == U2
    assert row.version == 2
    previous = await uow.user_entitlements.get(U1)
    assert previous.has_access is False
    assert previous.version == first.projection.version + 1
    # The key is locked first, then both users together in user_id order.
    assert uow.store_subscriptions.lock_calls[-1] == [
        "revenuecat:production:rc:shared:nutree_monthly"
    ]
    assert uow.user_entitlements.lock_calls[0] == sorted([U1, U2])


async def test_older_snapshot_does_not_steal_a_newer_row(service, uow, provider, clock):
    shared_sub = make_sub("shared")
    provider.responses[UID1] = snapshot(
        "shared", shared_sub, requested=UID1, fetched_at=NOW + timedelta(minutes=5)
    )
    clock.advance(minutes=5)
    await service.sync_user(U1, SyncLane.WEBHOOK, "u1 newer")
    # U2 has never synced, but its snapshot predates U1's row.
    provider.responses[UID2] = snapshot("shared", shared_sub, requested=UID2)

    result = await service.sync_user(U2, SyncLane.WEBHOOK, "u2 older")

    (row,) = uow.store_subscriptions.rows.values()
    assert row.user_id == U1
    assert result.projection.has_access is False
    assert (await uow.user_entitlements.get(U1)).has_access is True


async def test_alias_conflict_never_fails_the_sync(service, uow, provider):
    anonymous = "$RCAnonymousID:abc"
    await uow.billing_identities.add_alias(
        U2, "revenuecat", anonymous, IdentityKind.ANONYMOUS, seen_at=NOW
    )
    provider.responses[UID1] = snapshot(anonymous, make_sub(anonymous), requested=UID1)

    with patch.object(sync_module, "increment_metric") as metric:
        result = await service.sync_user(U1, SyncLane.WEBHOOK, "aliased")

    assert result.applied
    assert result.projection.has_access is True
    owner = await uow.billing_identities.find_by_customer_id("revenuecat", anonymous)
    assert owner.user_id == U2
    metric.assert_any_call("identity.alias_conflict")


async def test_failed_alias_insert_never_fails_the_sync(service, uow, provider):
    uow.billing_identities.fail_alias_inserts = True
    provider.responses[UID1] = snapshot("$RCAnonymousID:x", requested=UID1)

    result = await service.sync_user(U1, SyncLane.WEBHOOK, "aliased")

    assert result.applied
    assert result.projection.state == "none"


async def test_anonymous_canonical_is_linked_as_anonymous_alias(service, uow, provider):
    anonymous = "$RCAnonymousID:abc"
    provider.responses[UID1] = snapshot(anonymous, make_sub(anonymous), requested=UID1)

    await service.sync_user(U1, SyncLane.WEBHOOK, "aliased")

    identity = await uow.billing_identities.find_by_customer_id("revenuecat", anonymous)
    assert identity.user_id == U1
    assert identity.kind == IdentityKind.ANONYMOUS


async def test_primary_identity_conflict_is_logged_not_raised(service, uow, provider):
    outcome = await uow.billing_identities.upsert_primary(
        U2, "revenuecat", UID1, seen_at=NOW
    )
    assert outcome == IdentityLinkOutcome.INSERTED
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))

    with patch.object(sync_module, "increment_metric") as metric:
        result = await service.sync_user(U1, SyncLane.WEBHOOK, "conflict")

    assert result.applied
    metric.assert_any_call("identity.primary_conflict")


async def test_user_deleted_during_the_fetch_gets_nothing_written(
    service, uow, provider
):
    """D11: the delete handler takes the same user lock; _apply re-checks."""
    await uow.billing_identities.upsert_primary(U1, "revenuecat", UID1, seen_at=NOW)
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))

    def delete_account(_customer_id):
        # Account deletion commits while RevenueCat is answering.
        uow.users.users[U1] = make_user(U1, UID1, active=False)

    provider.on_fetch = delete_account
    await uow.billing_identities.retire_for_user(U1, retired_at=NOW)

    result = await service.sync_user(U1, SyncLane.WEBHOOK, "event:RENEWAL")

    assert result.sync_error == USER_NOT_FOUND and not result.applied
    assert await uow.store_subscriptions.list_for_user(U1) == []
    assert await uow.user_entitlements.get(U1) is None
    identity = await uow.billing_identities.find_by_customer_id(
        "revenuecat", UID1, include_retired=True
    )
    assert identity.retired_at == NOW


async def test_retired_primary_is_not_relinked_for_the_same_user(uow):
    await uow.billing_identities.upsert_primary(U1, "revenuecat", UID1, seen_at=NOW)
    await uow.billing_identities.retire_for_user(U1, retired_at=NOW)

    with patch.object(sync_module, "increment_metric") as metric:
        outcome = await uow.billing_identities.upsert_primary(
            U1, "revenuecat", UID1, seen_at=NOW
        )
        sync_module.record_primary_link_outcome(
            outcome, user_id=U1, provider="revenuecat"
        )

    assert outcome == IdentityLinkOutcome.RETIRED
    metric.assert_called_once_with("identity.primary_retired")
    assert await uow.billing_identities.get_primary(U1) is None


async def test_legacy_lead_customer_is_fetched_and_merged(service, uow, provider):
    await uow.billing_identities.add_alias(
        U1, "revenuecat", "lead-1", IdentityKind.LEGACY_LEAD, seen_at=NOW
    )
    provider.responses[UID1] = snapshot(UID1)
    provider.responses["lead-1"] = snapshot("lead-1", make_sub("lead-1", "web_yearly"))

    result = await service.sync_user(U1, SyncLane.BACKFILL, "backfill")

    assert provider.calls == [UID1, "lead-1"]
    assert result.projection.has_access is True
    assert result.projection.product_id == "web_yearly"


async def test_legacy_customer_with_foreign_canonical_id_is_rejected(
    service, uow, provider
):
    await uow.billing_identities.add_alias(
        U1, "revenuecat", "lead-1", IdentityKind.LEGACY_LEAD, seen_at=NOW
    )
    provider.responses[UID1] = snapshot(UID1)
    provider.responses["lead-1"] = snapshot(
        "someone-else", make_sub("someone-else"), requested="lead-1"
    )

    result = await service.sync_user(U1, SyncLane.BACKFILL, "backfill")

    assert result.projection.has_access is False
    assert await uow.store_subscriptions.list_for_user(U1) == []


async def test_legacy_fetch_failure_does_not_fail_the_sync(service, uow, provider):
    await uow.billing_identities.add_alias(
        U1, "revenuecat", "lead-1", IdentityKind.LEGACY_LEAD, seen_at=NOW
    )
    await uow.billing_identities.add_alias(
        U1, "revenuecat", "lead-2", IdentityKind.LEGACY_LEAD, seen_at=NOW
    )
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))
    provider.responses["lead-1"] = ProviderUnavailable("down")
    # lead-2 has no response: the provider answers 404, which is skipped.

    result = await service.sync_user(U1, SyncLane.RECONCILE, "reconcile")

    assert result.applied and not result.stale
    assert result.projection.has_access is True


async def test_aliased_legacy_customer_is_not_applied_twice(service, uow, provider):
    await uow.billing_identities.add_alias(
        U1, "revenuecat", "lead-1", IdentityKind.LEGACY_LEAD, seen_at=NOW
    )
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))
    provider.responses["lead-1"] = snapshot(UID1, make_sub(UID1), requested="lead-1")

    await service.sync_user(U1, SyncLane.BACKFILL, "backfill")

    assert len(await uow.store_subscriptions.list_for_user(U1)) == 1


async def test_inactive_or_unknown_user_is_not_synced(service, uow, provider):
    uow.users.users[U1] = make_user(U1, UID1, active=False)

    inactive = await service.sync_user(U1, SyncLane.WEBHOOK, "deleted")
    unknown = await service.sync_user("missing", SyncLane.WEBHOOK, "unknown")

    assert inactive.sync_error == USER_NOT_FOUND
    assert unknown.sync_error == USER_NOT_FOUND
    assert provider.calls == []


async def test_unknown_lane_is_rejected(service):
    with pytest.raises(ValueError):
        await service.sync_user(U1, "vip", "test")


async def test_sandbox_policy_none_denies_sandbox_purchase(
    uow, provider, budget, clock
):
    service = _service(
        uow,
        provider,
        budget,
        clock,
        access_config=EntitlementAccessConfig(sandbox_policy="none"),
    )
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1, environment="sandbox"))

    with patch.object(sync_module, "increment_metric") as metric:
        result = await service.sync_user(U1, SyncLane.WEBHOOK, "sandbox")

    assert result.projection.has_access is False
    metric.assert_any_call(
        "entitlement.mismatch",
        attributes={"direction": "provider_active_ledger_inactive"},
    )


async def test_sandbox_allowlist_grants_listed_user(uow, provider, budget, clock):
    service = _service(
        uow,
        provider,
        budget,
        clock,
        access_config=EntitlementAccessConfig(
            sandbox_policy="allowlist", sandbox_allowed_user_ids=frozenset({U1})
        ),
    )
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1, environment="sandbox"))

    result = await service.sync_user(U1, SyncLane.WEBHOOK, "sandbox")

    assert result.projection.has_access is True
    assert result.projection.is_sandbox is True


async def test_matching_verdicts_emit_no_mismatch(service, provider):
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))

    with patch.object(sync_module, "increment_metric") as metric:
        await service.sync_user(U1, SyncLane.WEBHOOK, "match")

    assert all(call.args[0] != "entitlement.mismatch" for call in metric.call_args_list)


async def test_grace_period_keeps_access(service, provider):
    provider.responses[UID1] = snapshot(
        UID1,
        make_sub(
            UID1,
            state="in_grace",
            expires_at=NOW - timedelta(hours=1),
            grace_expires_at=NOW + timedelta(days=6),
            billing_issue_detected_at=NOW - timedelta(hours=2),
        ),
    )

    result = await service.sync_user(U1, SyncLane.WEBHOOK, "grace")

    assert result.projection.has_access is True
    assert result.projection.state == "grace"
    assert result.projection.next_change_at == NOW + timedelta(days=6)


async def test_recompute_user_keeps_sync_time_and_bumps_version(
    service, uow, provider, clock
):
    provider.responses[UID1] = snapshot(
        UID1, make_sub(UID1, expires_at=NOW + timedelta(hours=1))
    )
    synced = (await service.sync_user(U1, SyncLane.WEBHOOK, "ok")).projection
    later = clock.advance(hours=2)

    recomputed = await service.recompute_user(U1)

    projection = recomputed[ENTITLEMENT_STANDARD]
    assert projection.has_access is False
    assert projection.state == "expired"
    assert projection.version == synced.version + 1
    assert projection.last_provider_sync_at == synced.last_provider_sync_at
    assert projection.computed_at == later
    assert uow.user_entitlements.lock_calls[-1] == [U1]


async def test_recompute_creates_rows_for_every_entitlement_key(service, uow, provider):
    provider.responses[UID1] = snapshot(
        UID1,
        make_sub(UID1, "nutree_monthly"),
        make_sub(UID1, "coach_addon", entitlement_key="coach"),
        make_sub(UID1, "orphan", entitlement_key=None),
    )

    result = await service.sync_user(U1, SyncLane.WEBHOOK, "multi")

    assert set(result.projections) == {"coach", ENTITLEMENT_STANDARD}
    assert result.projections["coach"].product_id == "coach_addon"


async def test_previous_hint_survives_unhinted_sync(service, uow, provider, clock):
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))
    await service.sync_user(U1, SyncLane.PURCHASE_SYNC, "p", purchase_hint=True)
    provider.responses[UID1] = replace(
        provider.responses[UID1], fetched_at=clock.advance(minutes=1)
    )

    result = await service.sync_user(U1, SyncLane.LOGIN_SYNC, "login")

    assert result.projection.last_purchase_hint_at == NOW


async def test_discarded_hinted_sync_still_records_the_hint(
    service, uow, provider, clock
):
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))
    first = await service.sync_user(U1, SyncLane.WEBHOOK, "fresh")
    hint_at = clock.advance(minutes=1)

    result = await service.sync_user(
        U1, SyncLane.PURCHASE_SYNC, "purchase", purchase_hint=True
    )

    assert result.discarded is True
    assert result.projection.last_purchase_hint_at == hint_at
    stored = await uow.user_entitlements.get(U1)
    assert stored.last_purchase_hint_at == hint_at
    assert stored.version == first.projection.version


async def test_duplicate_subscription_entries_are_applied_once(service, uow, provider):
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1), make_sub(UID1))

    await service.sync_user(U1, SyncLane.WEBHOOK, "dupes")

    assert len(await uow.store_subscriptions.list_for_user(U1)) == 1
