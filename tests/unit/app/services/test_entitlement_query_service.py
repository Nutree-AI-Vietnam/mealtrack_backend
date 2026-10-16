"""EntitlementQueryService and the /v1/me/entitlements contract DTO (NM-680)."""

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

from src.app.schemas.entitlement_schemas import EntitlementsView, iso_utc
from src.app.services.entitlement_query_service import EntitlementQueryService
from src.app.services.entitlement_sync_service import (
    EntitlementSyncResult,
    EntitlementSyncService,
)
from src.domain.model.billing.constants import (
    FLAG_ENT_READ_API,
    FLAG_ENT_READ_API_DEFER,
    SyncLane,
)
from src.domain.model.billing.ledger import UserEntitlement
from src.domain.ports.feature_flag_reader_port import FeatureFlagReaderPort

pytestmark = pytest.mark.asyncio

U1, UID1 = "user-1", "uid-1"


class StaticFlags(FeatureFlagReaderPort):
    def __init__(self, **flags: bool):
        self.flags = flags

    async def is_enabled(self, name: str, default: bool = False) -> bool:
        return self.flags.get(name, default)


@pytest.fixture
def uow():
    store = FakeUnitOfWork()
    store.users.users[U1] = make_user(U1, UID1)
    return store


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def provider():
    return FakeBillingProvider()


@pytest.fixture
def sync(uow, provider, clock):
    return EntitlementSyncService(
        uow_factory=lambda: uow, provider=provider, budget=FakeBudget(), clock=clock
    )


def _query(uow, sync, clock, **flags):
    return EntitlementQueryService(
        uow_factory=lambda: uow,
        sync_service=sync,
        flags=StaticFlags(**flags),
        clock=clock,
    )


@pytest.fixture
def query(uow, sync, clock):
    return _query(uow, sync, clock, **{FLAG_ENT_READ_API: True})


def _cancelled_projection(**overrides) -> UserEntitlement:
    values = {
        "user_id": U1,
        "entitlement_key": "standard",
        "has_access": True,
        "state": "cancelled_active",
        "primary_source": "store",
        "version": 42,
        "computed_at": NOW - timedelta(seconds=2),
        "access_until": NOW + timedelta(days=17, hours=16),
        "next_change_at": NOW + timedelta(days=17, hours=16),
        "will_renew": False,
        "period_type": "normal",
        "primary_ref": "rc:uid-1:nutree_2999_1y_0",
        "product_id": "nutree_2999_1y_0",
        "store": "app_store",
        "ever_had_access": True,
        "last_provider_sync_at": NOW - timedelta(seconds=2),
    }
    values.update(overrides)
    return UserEntitlement(**values)


async def test_contract_body_matches_shared_example(uow, query):
    await uow.user_entitlements.upsert(_cancelled_projection())

    view = await query.get_entitlements(U1)

    assert view.to_contract() == {
        "schema_version": 1,
        "authority": "backend",
        "version": 42,
        "server_time": "2026-10-15T08:00:00Z",
        "has_access": True,
        "state": "cancelled_active",
        "ever_subscribed": True,
        "entitlements": {
            "standard": {
                "active": True,
                "state": "cancelled_active",
                "access_until": "2026-11-02T00:00:00Z",
                "will_renew": False,
                "period_type": "normal",
                "product_id": "nutree_2999_1y_0",
                "store": "app_store",
                "source": "store",
                "is_sandbox": False,
            }
        },
        "synced_at": "2026-10-15T07:59:58Z",
        "stale": False,
        "sync_error": None,
        "ttl_seconds": 300,
    }


@pytest.mark.parametrize(
    "flags",
    [
        {},
        {FLAG_ENT_READ_API: False},
        {FLAG_ENT_READ_API: True, FLAG_ENT_READ_API_DEFER: True},
        {FLAG_ENT_READ_API_DEFER: True},
    ],
)
async def test_rc_authority_when_read_api_off_or_deferred(uow, sync, clock, flags):
    await uow.user_entitlements.upsert(_cancelled_projection())
    query = _query(uow, sync, clock, **flags)

    body = (await query.get_entitlements(U1)).to_contract()

    assert body["authority"] == "rc"
    assert body["state"] == "unknown"
    assert body["has_access"] is False
    assert body["entitlements"] == {}
    assert body["version"] == 0
    assert body["schema_version"] == 1


async def test_never_synced_user_is_unknown_with_backend_authority(query):
    body = (await query.get_entitlements(U1)).to_contract()

    assert body["authority"] == "backend"
    assert body["state"] == "unknown"
    assert body["has_access"] is False
    assert body["synced_at"] is None
    assert await query.has_access(U1) is None


async def test_read_time_expiry_recomputes_without_reconciler(
    uow, query, sync, provider, clock
):
    provider.responses[UID1] = snapshot(
        UID1, make_sub(UID1, expires_at=NOW + timedelta(hours=1))
    )
    synced = (await sync.sync_user(U1, SyncLane.WEBHOOK, "bought")).projection
    assert synced.has_access is True
    clock.advance(hours=1, seconds=1)

    view = await query.get_entitlements(U1)

    assert view.has_access is False
    assert view.state == "expired"
    assert view.ever_subscribed is True
    stored = await uow.user_entitlements.get(U1)
    assert stored.has_access is False
    assert stored.version == synced.version + 1
    # The ETag changes once next_change_at has passed.
    assert view.etag != EntitlementsView.from_projections(U1, [synced], NOW).etag
    assert provider.calls == [UID1]


async def test_cached_yes_past_access_until_reads_no_even_without_recompute(query, uow):
    stale_yes = _cancelled_projection(
        access_until=NOW - timedelta(minutes=1), next_change_at=None
    )
    await uow.user_entitlements.upsert(stale_yes)

    view = await query.get_entitlements(U1)

    assert view.has_access is False
    assert view.state == "expired"
    assert view.entitlements["standard"].active is False


async def test_lifetime_has_null_access_until(query, uow):
    await uow.user_entitlements.upsert(
        _cancelled_projection(
            state="active",
            is_lifetime=True,
            access_until=None,
            next_change_at=None,
        )
    )

    body = (await query.get_entitlements(U1)).to_contract()

    assert body["has_access"] is True
    assert body["entitlements"]["standard"]["access_until"] is None


async def test_view_for_sync_carries_stale_and_error(query, uow):
    projection = _cancelled_projection()
    result = EntitlementSyncResult(
        user_id=U1,
        projections={"standard": projection},
        stale=True,
        sync_error="provider_unavailable",
    )

    body = (await query.view_for_sync(result)).to_contract()

    assert body["has_access"] is True
    assert body["stale"] is True
    assert body["sync_error"] == "provider_unavailable"


async def test_view_for_sync_rc_authority_keeps_error(uow, sync, clock):
    query = _query(uow, sync, clock)
    result = EntitlementSyncResult(
        user_id=U1, stale=True, sync_error="provider_unavailable"
    )

    body = (await query.view_for_sync(result)).to_contract()

    assert body["authority"] == "rc"
    assert body["stale"] is True
    assert body["sync_error"] == "provider_unavailable"


async def test_view_for_sync_applies_read_time_rule(query, uow, sync, provider, clock):
    provider.responses[UID1] = snapshot(
        UID1, make_sub(UID1, expires_at=NOW + timedelta(minutes=1))
    )
    result = await sync.sync_user(U1, SyncLane.WEBHOOK, "bought")
    clock.advance(minutes=2)

    view = await query.view_for_sync(result)

    assert view.has_access is False


async def test_etag_and_cacheability():
    projection = _cancelled_projection()
    view = EntitlementsView.from_projections(U1, [projection], NOW)
    epoch = int(projection.next_change_at.timestamp())

    assert view.etag == f'W/"{U1}:42:{epoch}"'
    assert view.is_cacheable_at(NOW) is True
    assert view.is_cacheable_at(projection.next_change_at) is False
    rc = EntitlementsView.rc_authority(U1, NOW)
    assert rc.etag == f'W/"{U1}:rc"'
    no_change = EntitlementsView.from_projections(
        U1, [_cancelled_projection(next_change_at=None)], NOW
    )
    assert no_change.etag == f'W/"{U1}:42:0"'
    assert no_change.is_cacheable_at(NOW + timedelta(days=999)) is True


async def test_has_access_reads_projection_with_read_time_rule(query, uow):
    await uow.user_entitlements.upsert(_cancelled_projection())

    assert await query.has_access(U1) is True
    assert await query.has_access(U1, "coach") is None


async def test_debug_view_lists_ledger_rows(query, sync, provider):
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))
    await sync.sync_user(U1, SyncLane.WEBHOOK, "bought")

    debug = await query.get_debug_view(U1)

    assert [p.entitlement_key for p in debug.projections] == ["standard"]
    assert len(debug.store_subscriptions) == 1
    assert debug.grants == [] and debug.events == []


async def test_iso_utc_formats_with_z_suffix():
    assert iso_utc(None) is None
    assert iso_utc(NOW.replace(tzinfo=None)) == "2026-10-15T08:00:00Z"
    assert iso_utc(NOW + timedelta(microseconds=999)) == "2026-10-15T08:00:00Z"


async def test_never_synced_user_without_access_reads_unknown(query, uow):
    # e.g. a manual grant that has lapsed, with no provider fetch yet.
    await uow.user_entitlements.upsert(
        _cancelled_projection(
            has_access=False,
            state="expired",
            primary_source="grant",
            access_until=NOW - timedelta(days=1),
            next_change_at=None,
            last_provider_sync_at=None,
        )
    )

    view = await query.get_entitlements(U1)

    assert view.authority == "backend"
    assert view.state == "unknown"
    assert view.has_access is False
    assert await query.has_access(U1) is None


async def test_never_synced_user_with_live_grant_reads_backend_access(query, uow):
    await uow.user_entitlements.upsert(
        _cancelled_projection(
            state="active", primary_source="grant", last_provider_sync_at=None
        )
    )

    view = await query.get_entitlements(U1)

    assert (view.state, view.has_access) == ("active", True)
    assert view.to_contract()["entitlements"]["standard"]["source"] == "grant"
