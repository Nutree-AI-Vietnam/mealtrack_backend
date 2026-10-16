"""A legacy id that is (or merged into) another account never hijacks it (NM-680).

Regression for the TRANSFER row shape: legacy ``handle_transfer`` rewrites
``subscriptions.revenuecat_subscriber_id`` to the target's Firebase UID but
keeps ``user_id`` on the source user, so the backfill used to seed the target's
UID as the source user's ``legacy_lead`` identity. The source user then got the
target's purchase, the target's webhooks resolved to the source user, the
target could not claim its own primary identity, and every source sync moved
the target's store row back.
"""

from dataclasses import replace

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

from src.app.services.entitlement_backfill_service import (
    BackfillCandidate,
    EntitlementBackfillService,
    is_seedable_legacy_id,
)
from src.app.services.entitlement_inbox_service import EntitlementInboxService
from src.app.services.entitlement_sync_service import EntitlementSyncService
from src.domain.model.billing.constants import (
    PROVIDER_REVENUECAT,
    IdentityKind,
    SyncLane,
)
from src.domain.ports.entitlement_ledger_repository_port import IdentityLinkOutcome

pytestmark = pytest.mark.asyncio

X, Y = "user-x", "user-y"
UID_X, UID_Y = "uid-x", "uid-y"
ANON = "$RCAnonymousID:0f3e"
LEAD = "6f1c1f0e-8f5e-4a49-9d0a-1c7b3b0f0a52"


class _Env:
    def __init__(self, uid_y: str = UID_Y):
        self.uid_y = uid_y
        self.uow = FakeUnitOfWork()
        self.uow.users.users[X] = make_user(X, UID_X)
        self.uow.users.users[Y] = make_user(Y, uid_y)
        self.clock = FakeClock(NOW)
        self.provider = FakeBillingProvider()
        self.budget = FakeBudget()
        self.sync = EntitlementSyncService(
            uow_factory=lambda: self.uow,
            provider=self.provider,
            budget=self.budget,
            clock=self.clock,
        )
        self.inbox = EntitlementInboxService(
            uow_factory=lambda: self.uow, sync_service=self.sync, clock=self.clock
        )
        self.backfill = EntitlementBackfillService(
            uow_factory=lambda: self.uow,
            sync_service=self.sync,
            provider=self.provider,
            budget=self.budget,
            clock=self.clock,
        )
        self.rc_state()

    def rc_state(self):
        """X owns nothing in RevenueCat any more; Y owns an active purchase."""
        at = self.clock.now
        self.provider.responses[UID_X] = snapshot(UID_X, fetched_at=at)
        self.provider.responses[self.uid_y] = snapshot(
            self.uid_y, make_sub(self.uid_y), fetched_at=at
        )

    async def later_sync(self, user_id: str):
        self.clock.advance(minutes=5)
        self.rc_state()
        return await self.sync.sync_user(user_id, SyncLane.LOGIN_SYNC, "client_sync")

    async def has_access(self, user_id: str) -> bool:
        row = await self.uow.user_entitlements.get(user_id)
        return bool(row and row.has_access)


def test_only_anonymous_ids_and_lead_uuids_are_seedable():
    assert is_seedable_legacy_id(ANON)
    assert is_seedable_legacy_id(LEAD)
    assert is_seedable_legacy_id(LEAD.upper())
    assert not is_seedable_legacy_id("$RCAnonymousID:")
    assert not is_seedable_legacy_id(UID_Y)
    assert not is_seedable_legacy_id(LEAD.replace("-", ""))


async def test_backfill_with_the_transfer_targets_uid_hijacks_nothing():
    env = _Env()

    row = await env.backfill.backfill(
        BackfillCandidate(user_id=X, firebase_uid=UID_X, legacy_customer_ids=(UID_Y,)),
        dry_run=False,
    )

    assert (
        await env.uow.billing_identities.find_by_customer_id(PROVIDER_REVENUECAT, UID_Y)
        is None
    )
    assert env.provider.calls == [UID_X]
    assert row.ledger_after is False and "legacy_ids_skipped:1" in row.notes
    resolution = await env.inbox.resolve_users(
        env.uow, {"type": "RENEWAL", "app_user_id": UID_Y}
    )
    assert resolution.user_ids == (Y,)


async def test_backfill_skips_a_uuid_shaped_firebase_uid_of_another_account():
    uid_y = "3d0c2c8e-1d8a-4f4e-9a55-5b6a2e9f7c10"
    env = _Env(uid_y=uid_y)

    for dry_run in (True, False):
        row = await env.backfill.backfill(
            BackfillCandidate(
                user_id=X, firebase_uid=UID_X, legacy_customer_ids=(uid_y,)
            ),
            dry_run=dry_run,
        )
        assert row.ledger_after is False
        assert row.legacy_customer_ids == 0

    assert uid_y not in env.provider.calls
    assert env.uow.billing_identities.rows == {} or all(
        identity.provider_customer_id != uid_y
        for identity in env.uow.billing_identities.rows.values()
    )


async def test_a_seeded_hijack_identity_is_undone_by_the_owner():
    """Data seeded before the fix: X holds Y's UID as a legacy lead."""
    env = _Env()
    await env.uow.billing_identities.add_alias(
        X, PROVIDER_REVENUECAT, UID_Y, IdentityKind.LEGACY_LEAD, seen_at=NOW
    )

    # Y's own webhook resolves to Y: an exact Firebase UID beats a legacy lead.
    resolution = await env.inbox.resolve_users(
        env.uow, {"type": "RENEWAL", "app_user_id": UID_Y}
    )
    assert resolution.user_ids == (Y,)

    # X's sync fetches the stale legacy id but RevenueCat lists it as Y's.
    await env.later_sync(X)
    assert not await env.has_access(X)

    # Y signs in: its own UID becomes its primary identity again.
    assert await env.inbox.link_primary_identity(Y, UID_Y) == (
        IdentityLinkOutcome.RECLAIMED
    )
    primary = await env.uow.billing_identities.get_primary(Y)
    assert primary is not None and primary.provider_customer_id == UID_Y
    await env.later_sync(Y)
    assert await env.has_access(Y)

    # X's later syncs no longer fetch Y's customer or move its row.
    calls_before = len(env.provider.calls)
    await env.later_sync(X)
    assert env.provider.calls[calls_before:] == [UID_X]
    assert await env.has_access(Y) and not await env.has_access(X)


async def test_the_sync_itself_reclaims_the_primary_identity():
    env = _Env()
    await env.uow.billing_identities.add_alias(
        X, PROVIDER_REVENUECAT, UID_Y, IdentityKind.LEGACY_LEAD, seen_at=NOW
    )

    await env.later_sync(Y)

    identity = await env.uow.billing_identities.find_by_customer_id(
        PROVIDER_REVENUECAT, UID_Y
    )
    assert identity.user_id == Y and identity.kind == IdentityKind.PRIMARY
    assert await env.has_access(Y)


async def test_upsert_primary_never_takes_another_users_primary():
    env = _Env()
    await env.uow.billing_identities.upsert_primary(
        Y, PROVIDER_REVENUECAT, UID_Y, seen_at=NOW
    )

    outcome = await env.uow.billing_identities.upsert_primary(
        X, PROVIDER_REVENUECAT, UID_Y, seen_at=NOW
    )

    assert outcome == IdentityLinkOutcome.CONFLICT
    identity = await env.uow.billing_identities.find_by_customer_id(
        PROVIDER_REVENUECAT, UID_Y
    )
    assert identity.user_id == Y


async def test_anonymous_legacy_customer_merged_into_another_account_is_rejected():
    """Y later logged in on the device that owns X's legacy anonymous id."""
    env = _Env()
    await env.uow.billing_identities.add_alias(
        X, PROVIDER_REVENUECAT, ANON, IdentityKind.LEGACY_LEAD, seen_at=NOW
    )
    env.provider.responses[ANON] = replace(
        snapshot(ANON, make_sub(ANON), fetched_at=env.clock.now), aliases=[UID_Y]
    )

    result = await env.sync.sync_user(X, SyncLane.LOGIN_SYNC, "client_sync")

    assert ANON in env.provider.calls
    assert result.projection is not None and not result.projection.has_access
    assert env.uow.store_subscriptions.rows == {}


async def test_a_legacy_snapshot_never_takes_a_row_from_another_user():
    """Primary beats legacy even when nothing else marks the customer as Y's."""
    env = _Env()
    sub = make_sub(LEAD)
    # Y's own customer reports the purchase (its canonical id is the lead).
    env.provider.responses[UID_Y] = snapshot(
        LEAD, sub, fetched_at=env.clock.now, requested=UID_Y
    )
    env.uow.billing_identities.fail_alias_inserts = True
    await env.sync.sync_user(Y, SyncLane.LOGIN_SYNC, "client_sync")
    env.uow.billing_identities.fail_alias_inserts = False
    assert await env.has_access(Y)

    await env.uow.billing_identities.add_alias(
        X, PROVIDER_REVENUECAT, LEAD, IdentityKind.LEGACY_LEAD, seen_at=NOW
    )
    env.clock.advance(minutes=1)
    env.provider.responses[UID_X] = snapshot(UID_X, fetched_at=env.clock.now)
    env.provider.responses[LEAD] = snapshot(LEAD, sub, fetched_at=env.clock.now)
    await env.sync.sync_user(X, SyncLane.LOGIN_SYNC, "client_sync")

    assert LEAD in env.provider.calls
    (row,) = env.uow.store_subscriptions.rows.values()
    assert row.user_id == Y
    assert await env.has_access(Y) and not await env.has_access(X)
