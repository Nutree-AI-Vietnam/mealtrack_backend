"""Entitlement ledger repositories against real Postgres: constraints, partial
indexes, upserts, inbox leasing and advisory locks (NM-680)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.domain.model.billing.constants import IdentityKind
from src.domain.model.billing.ledger import (
    BillingProviderEvent,
    EntitlementGrant,
    StoreSubscription,
    UserEntitlement,
)
from src.domain.ports.entitlement_ledger_repository_port import IdentityLinkOutcome
from src.infra.database.models.user.user import User
from src.infra.repositories.billing import (
    AsyncBillingCustomerIdentityRepository,
    AsyncBillingProviderEventRepository,
    AsyncEntitlementGrantRepository,
    AsyncStoreSubscriptionRepository,
    AsyncUserEntitlementRepository,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)


async def _user(session, *, active: bool = True) -> str:
    user_id = str(uuid4())
    session.add(
        User(
            id=user_id,
            firebase_uid=f"fb-{user_id}",
            email=f"{user_id}@example.test",
            username=user_id,
            password_hash="test",
            is_active=active,
        )
    )
    await session.flush()
    return user_id


async def _still_usable(session) -> bool:
    return (await session.execute(text("SELECT 1"))).scalar_one() == 1


def _store_row(
    user_id: str, key: str = "rc:c1:monthly", **overrides
) -> StoreSubscription:
    values = {
        "id": str(uuid4()),
        "user_id": user_id,
        "provider": "revenuecat",
        "store": "app_store",
        "environment": "production",
        "subscription_key": key,
        "source_customer_id": "c1",
        "product_id": "monthly",
        "state": "active",
        "fetched_at": NOW,
        "last_synced_at": NOW,
        "entitlement_key": "standard",
        "expires_at": NOW + timedelta(days=30),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return StoreSubscription(**values)


def _event(provider_event_id: str, **overrides) -> BillingProviderEvent:
    values = {
        "id": str(uuid4()),
        "provider": "revenuecat",
        "provider_event_id": provider_event_id,
        "event_type": "RENEWAL",
        "status": "received",
        "received_at": NOW,
        "raw": {"event": {"id": provider_event_id}},
    }
    values.update(overrides)
    return BillingProviderEvent(**values)


def _grant(user_id: str, **overrides) -> EntitlementGrant:
    values = {
        "id": str(uuid4()),
        "user_id": user_id,
        "entitlement_key": "standard",
        "source": "support",
        "reason": "goodwill",
        "starts_at": NOW,
        "ends_at": NOW + timedelta(days=30),
        "created_by": "admin@nutreeai.com",
    }
    values.update(overrides)
    return EntitlementGrant(**values)


# ---------------------------------------------------------------- identities


async def test_primary_identity_is_unique_per_customer_and_user(pg_session):
    repo = AsyncBillingCustomerIdentityRepository(pg_session)
    alice, bob = await _user(pg_session), await _user(pg_session)

    assert (
        await repo.upsert_primary(alice, "revenuecat", "fb-a", seen_at=NOW)
        == IdentityLinkOutcome.INSERTED
    )
    assert (
        await repo.upsert_primary(alice, "revenuecat", "fb-a", seen_at=NOW)
        == IdentityLinkOutcome.ALREADY_LINKED
    )
    assert (
        await repo.upsert_primary(bob, "revenuecat", "fb-a", seen_at=NOW)
        == IdentityLinkOutcome.CONFLICT
    )
    # A new primary id for Alice demotes the old one: one live primary per user.
    await repo.upsert_primary(alice, "revenuecat", "fb-a2", seen_at=NOW)
    identities = await repo.list_for_user(alice)
    assert [(i.provider_customer_id, i.kind) for i in identities] == [
        ("fb-a2", "primary"),
        ("fb-a", "alias"),
    ]


async def test_one_live_primary_index_is_enforced_by_postgres(pg_session):
    user_id = await _user(pg_session)
    insert = text(
        "INSERT INTO billing_customer_identities (id, user_id, provider, project, "
        "provider_customer_id, kind, retired_at, first_seen_at, last_seen_at, "
        "created_at, updated_at) VALUES (:id, :user_id, 'revenuecat', 'default', "
        ":cid, 'primary', :retired_at, now(), now(), now(), now())"
    )
    await pg_session.execute(
        insert,
        {"id": str(uuid4()), "user_id": user_id, "cid": "a", "retired_at": NOW},
    )
    await pg_session.execute(
        insert, {"id": str(uuid4()), "user_id": user_id, "cid": "b", "retired_at": None}
    )
    await pg_session.flush()

    with pytest.raises(IntegrityError, match="uq_bci_one_primary"):
        async with pg_session.begin_nested():
            await pg_session.execute(
                insert,
                {
                    "id": str(uuid4()),
                    "user_id": user_id,
                    "cid": "c",
                    "retired_at": None,
                },
            )


async def test_alias_conflict_returns_outcome_and_keeps_transaction(pg_session):
    repo = AsyncBillingCustomerIdentityRepository(pg_session)
    alice, bob = await _user(pg_session), await _user(pg_session)
    anon = "$RCAnonymousID:abc"

    assert (
        await repo.add_alias(
            alice, "revenuecat", anon, IdentityKind.ANONYMOUS, seen_at=NOW
        )
        == IdentityLinkOutcome.INSERTED
    )
    assert (
        await repo.add_alias(
            alice,
            "revenuecat",
            anon,
            IdentityKind.ANONYMOUS,
            seen_at=NOW + timedelta(1),
        )
        == IdentityLinkOutcome.ALREADY_LINKED
    )
    assert (
        await repo.add_alias(bob, "revenuecat", anon, IdentityKind.ALIAS, seen_at=NOW)
        == IdentityLinkOutcome.CONFLICT
    )
    assert await _still_usable(pg_session)
    found = await repo.find_by_customer_id("revenuecat", anon)
    assert found.user_id == alice
    assert found.last_seen_at == NOW + timedelta(1)
    with pytest.raises(ValueError):
        await repo.add_alias(
            alice, "revenuecat", "x", IdentityKind.PRIMARY, seen_at=NOW
        )


async def test_primary_reclaims_own_uid_from_another_users_non_primary_row(
    pg_session,
):
    repo = AsyncBillingCustomerIdentityRepository(pg_session)
    source, payer = await _user(pg_session), await _user(pg_session)
    await repo.add_alias(
        source, "revenuecat", "fb-payer", IdentityKind.LEGACY_LEAD, seen_at=NOW
    )
    await repo.upsert_primary(payer, "revenuecat", "fb-payer-old", seen_at=NOW)

    assert (
        await repo.upsert_primary(
            payer, "revenuecat", "fb-payer", seen_at=NOW + timedelta(1)
        )
        == IdentityLinkOutcome.RECLAIMED
    )

    primary = await repo.get_primary(payer)
    assert primary.provider_customer_id == "fb-payer"
    assert [i.provider_customer_id for i in await repo.list_for_user(source)] == []
    # The payer's earlier primary was demoted, so the one-primary index holds.
    kinds = {i.provider_customer_id: i.kind for i in await repo.list_for_user(payer)}
    assert kinds == {"fb-payer": "primary", "fb-payer-old": "alias"}
    # Another user's live primary is never taken.
    assert (
        await repo.upsert_primary(source, "revenuecat", "fb-payer", seen_at=NOW)
        == IdentityLinkOutcome.CONFLICT
    )


async def test_retired_identity_is_reclaimed_by_reregistered_account(pg_session):
    repo = AsyncBillingCustomerIdentityRepository(pg_session)
    deleted, fresh = await _user(pg_session), await _user(pg_session)
    await repo.upsert_primary(deleted, "revenuecat", "fb-same", seen_at=NOW)

    assert await repo.retire_for_user(deleted, retired_at=NOW) == 1
    assert await repo.get_primary(deleted) is None
    assert await repo.find_by_customer_id("revenuecat", "fb-same") is None
    assert (
        await repo.upsert_primary(fresh, "revenuecat", "fb-same", seen_at=NOW)
        == IdentityLinkOutcome.INSERTED
    )

    primary = await repo.get_primary(fresh)
    assert primary.provider_customer_id == "fb-same"
    assert primary.retired_at is None


async def test_retired_identity_is_never_unretired_for_the_deleted_account(
    pg_session,
):
    """D11: only a different, re-registered user may reclaim a retired id."""
    repo = AsyncBillingCustomerIdentityRepository(pg_session)
    deleted = await _user(pg_session)
    await repo.upsert_primary(deleted, "revenuecat", "fb-gone", seen_at=NOW)
    await repo.retire_for_user(deleted, retired_at=NOW)

    assert (
        await repo.upsert_primary(
            deleted, "revenuecat", "fb-gone", seen_at=NOW + timedelta(1)
        )
        == IdentityLinkOutcome.RETIRED
    )

    row = await repo.find_by_customer_id("revenuecat", "fb-gone", include_retired=True)
    assert row.user_id == deleted and row.retired_at == NOW
    assert await repo.get_primary(deleted) is None


# ---------------------------------------------------------------- inbox


async def test_inbox_dedupes_on_provider_event_id(pg_session):
    repo = AsyncBillingProviderEventRepository(pg_session)

    assert await repo.insert_if_absent(_event("evt-1")) is True
    assert await repo.insert_if_absent(_event("evt-1", event_type="RENEWAL")) is False
    assert await repo.insert_if_absent(_event("evt-1", provider="other")) is True
    stored = await repo.get_by_provider_event_id("revenuecat", "evt-1")
    assert stored.raw == {"event": {"id": "evt-1"}}
    assert await _still_usable(pg_session)


async def test_inbox_status_check_constraint(pg_session):
    repo = AsyncBillingProviderEventRepository(pg_session)

    with pytest.raises(IntegrityError, match="ck_bpe_status"):
        async with pg_session.begin_nested():
            await repo.insert_if_absent(_event("evt-bad", status="done"))


async def test_claim_due_skips_rows_locked_by_another_worker(
    pg_session, async_session_factory
):
    repo = AsyncBillingProviderEventRepository(pg_session)
    for n in range(3):
        await repo.insert_if_absent(
            _event(f"evt-{n}", received_at=NOW + timedelta(seconds=n))
        )
    await repo.insert_if_absent(
        _event("evt-later", next_attempt_at=NOW + timedelta(hours=1))
    )
    await repo.insert_if_absent(_event("evt-done", status="processed"))
    await pg_session.commit()

    async with async_session_factory() as worker_a, async_session_factory() as worker_b:
        claimed_a = await AsyncBillingProviderEventRepository(worker_a).claim_due(
            now=NOW,
            limit=2,
            lease_owner="a",
            lease_expires_at=NOW + timedelta(minutes=5),
        )
        claimed_b = await AsyncBillingProviderEventRepository(worker_b).claim_due(
            now=NOW,
            limit=5,
            lease_owner="b",
            lease_expires_at=NOW + timedelta(minutes=5),
        )
        await worker_a.commit()
        await worker_b.commit()

    assert [e.provider_event_id for e in claimed_a] == ["evt-0", "evt-1"]
    assert [e.provider_event_id for e in claimed_b] == ["evt-2"]
    assert {e.status for e in claimed_a + claimed_b} == {"processing"}


async def test_expired_lease_is_reclaimed_and_attempts_counted(pg_session):
    repo = AsyncBillingProviderEventRepository(pg_session)
    event = _event("evt-crash")
    await repo.insert_if_absent(event)
    await repo.claim_due(
        now=NOW,
        limit=1,
        lease_owner="dead",
        lease_expires_at=NOW + timedelta(minutes=1),
    )
    assert (
        await repo.claim_due(
            now=NOW,
            limit=1,
            lease_owner="b",
            lease_expires_at=NOW + timedelta(minutes=5),
        )
        == []
    )

    (reclaimed,) = await repo.claim_due(
        now=NOW + timedelta(minutes=2),
        limit=1,
        lease_owner="b",
        lease_expires_at=NOW + timedelta(minutes=7),
    )
    assert reclaimed.lease_owner == "b"

    await repo.mark_retry(event.id, error="provider_unavailable", next_attempt_at=NOW)
    await repo.mark_retry(event.id, error="provider_unavailable", next_attempt_at=NOW)
    await repo.mark_dead(event.id, error="gave up")
    dead = await repo.get(event.id)
    assert (dead.status, dead.attempts, dead.lease_owner) == ("dead", 3, None)


async def test_park_requeue_and_settle(pg_session):
    repo = AsyncBillingProviderEventRepository(pg_session)
    user_id = await _user(pg_session)
    event = _event("evt-park", app_user_id="fb-unknown")
    await repo.insert_if_absent(event)

    await repo.park(event.id, reason="user_not_found")
    (parked,) = await repo.list_unmatched(limit=10, app_user_ids=["fb-unknown"])
    assert parked.park_reason == "user_not_found"
    assert await repo.list_unmatched(limit=10, app_user_ids=[]) == []

    assert await repo.requeue([event.id], user_id=user_id, now=NOW) == 1
    assert await repo.requeue([event.id], user_id=user_id, now=NOW) == 0
    await repo.mark_processed(event.id, processed_at=NOW)
    settled = await repo.get(event.id)
    assert (settled.status, settled.user_id, settled.park_reason) == (
        "processed",
        user_id,
        None,
    )
    assert settled.is_settled
    assert [e.id for e in await repo.list_for_user(user_id)] == [event.id]

    other = _event("evt-test")
    await repo.insert_if_absent(other)
    await repo.mark_ignored(other.id, reason="TEST event")
    assert (await repo.get(other.id)).status == "ignored"


async def test_set_user_and_raw_purge_for_a_deleted_account(pg_session):
    repo = AsyncBillingProviderEventRepository(pg_session)
    gone, other = await _user(pg_session), await _user(pg_session)
    own = _event("evt-own")
    parked = _event("evt-parked", app_user_id="$RCAnonymousID:gone")
    foreign = _event("evt-foreign", app_user_id="$RCAnonymousID:gone")
    later = _event("evt-later", app_user_id="fb-gone")
    for event in (own, parked, foreign, later):
        await repo.insert_if_absent(event)
    await repo.set_user(own.id, gone)
    await repo.park(parked.id, reason="user_not_found")
    await repo.set_user(foreign.id, other)  # another user's processed row

    purged = await repo.purge_raw_for_user(
        gone, app_user_ids=["$RCAnonymousID:gone", "fb-gone"]
    )
    await repo.park(later.id, reason="deleted_user", drop_raw=True)

    assert purged == 2
    assert (await repo.get(own.id)).raw is None
    assert (await repo.get(parked.id)).raw is None
    assert (await repo.get(foreign.id)).raw is not None
    assert (await repo.get(later.id)).raw is None
    assert (await repo.get(own.id)).user_id == gone
    # SQL NULL, not a JSON 'null' body (the retention index relies on it).
    bodies = await pg_session.execute(
        text(
            "SELECT count(*) FROM billing_provider_events "
            "WHERE raw IS NULL AND id = ANY(:ids)"
        ),
        {"ids": [own.id, parked.id, later.id]},
    )
    assert bodies.scalar_one() == 3


async def test_raw_retention_purges_oldest_bodies_in_batches(
    pg_session, async_session_factory
):
    repo = AsyncBillingProviderEventRepository(pg_session)
    cutoff = NOW - timedelta(days=90)
    old = [
        _event(f"evt-old-{n}", received_at=cutoff - timedelta(days=n + 1))
        for n in range(3)
    ]
    recent = _event("evt-recent", received_at=cutoff + timedelta(seconds=1))
    for event in (*old, recent):
        await repo.insert_if_absent(event)
    await pg_session.commit()

    # A row another transaction holds is skipped, not waited for.
    async with async_session_factory() as other:
        await other.execute(
            text("SELECT 1 FROM billing_provider_events WHERE id = :id FOR UPDATE"),
            {"id": old[0].id},
        )
        assert await repo.purge_raw_received_before(cutoff, limit=1) == 1
        assert await repo.purge_raw_received_before(cutoff, limit=10) == 1
        await other.rollback()
    assert await repo.purge_raw_received_before(cutoff, limit=10) == 1

    assert [(await repo.get(event.id)).raw for event in old] == [None, None, None]
    assert (await repo.get(recent.id)).raw is not None


# ---------------------------------------------------------------- store subs


async def test_store_subscription_upsert_keeps_one_row_per_key(pg_session):
    repo = AsyncStoreSubscriptionRepository(pg_session)
    alice, bob = await _user(pg_session), await _user(pg_session)
    first = await repo.upsert(_store_row(alice))

    moved = await repo.upsert(
        _store_row(bob, id=str(uuid4()), state="in_grace", version=2)
    )

    assert moved.id == first.id
    assert moved.created_at == first.created_at
    assert (moved.user_id, moved.state, moved.version) == (bob, "in_grace", 2)
    assert await repo.list_for_user(alice) == []
    assert (
        len(await repo.get_by_keys("revenuecat", [("production", "rc:c1:monthly")]))
        == 1
    )
    # Same key in the sandbox environment is a different subscription.
    await repo.upsert(_store_row(bob, environment="sandbox"))
    assert len(await repo.list_for_user(bob)) == 2


async def test_store_subscription_upsert_many_and_queries(pg_session):
    repo = AsyncStoreSubscriptionRepository(pg_session)
    user_id = await _user(pg_session)
    rows = [
        _store_row(user_id, "rc:c1:a", product_id="a"),
        _store_row(user_id, "rc:c1:b", product_id="b", state="absent"),
        _store_row(user_id, "rc:c2:c", product_id="c", source_customer_id="c2"),
    ]

    await repo.upsert_many(rows)
    await repo.upsert_many([])

    assert {r.product_id for r in await repo.list_for_user(user_id)} == {"a", "b", "c"}
    assert {
        r.product_id for r in await repo.list_for_user(user_id, include_absent=False)
    } == {"a", "c"}
    assert {r.product_id for r in await repo.list_for_customer("revenuecat", "c1")} == {
        "a",
        "b",
    }
    assert await repo.get_by_keys("revenuecat", []) == []


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"state": "cancelled"}, "ck_ss_state"),
        ({"environment": "staging"}, "ck_ss_environment"),
    ],
)
async def test_store_subscription_check_constraints(pg_session, overrides, constraint):
    repo = AsyncStoreSubscriptionRepository(pg_session)
    user_id = await _user(pg_session)

    with pytest.raises(IntegrityError, match=constraint):
        async with pg_session.begin_nested():
            await repo.upsert(_store_row(user_id, **overrides))


async def test_key_locks_serialise_two_transactions(async_session_factory, pg_session):
    keys = [("production", "rc:c1:monthly")]
    async with async_session_factory() as holder, async_session_factory() as other:
        await AsyncStoreSubscriptionRepository(holder).lock_keys("revenuecat", keys)
        # Same lock as lock_keys takes for that key.
        probe = await other.execute(
            text("SELECT pg_try_advisory_xact_lock(680, hashtext(:k))"),
            {"k": "revenuecat:production:rc:c1:monthly"},
        )
        assert probe.scalar_one() is False
        await holder.rollback()
        probe = await other.execute(
            text("SELECT pg_try_advisory_xact_lock(680, hashtext(:k))"),
            {"k": "revenuecat:production:rc:c1:monthly"},
        )
        assert probe.scalar_one() is True
        await other.rollback()


# ---------------------------------------------------------------- grants


async def test_grant_window_checks(pg_session):
    repo = AsyncEntitlementGrantRepository(pg_session)
    user_id = await _user(pg_session)
    await repo.add(_grant(user_id, ends_at=NOW + timedelta(days=366)))

    with pytest.raises(IntegrityError, match="ck_eg_window_max"):
        async with pg_session.begin_nested():
            await repo.add(
                _grant(user_id, ends_at=NOW + timedelta(days=366, seconds=1))
            )
    with pytest.raises(IntegrityError, match="ck_eg_window_positive"):
        async with pg_session.begin_nested():
            await repo.add(_grant(user_id, ends_at=NOW))
    with pytest.raises(IntegrityError, match="ck_eg_source"):
        async with pg_session.begin_nested():
            await repo.add(_grant(user_id, source="promo"))


async def test_grant_source_ref_is_unique_only_when_present(pg_session):
    repo = AsyncEntitlementGrantRepository(pg_session)
    user_id = await _user(pg_session)
    await repo.add(_grant(user_id))
    await repo.add(_grant(user_id))  # NULL source_ref: no uniqueness
    first = await repo.add(_grant(user_id, source_ref="ticket-1"))
    await repo.add(_grant(user_id, source_ref="ticket-1", entitlement_key="coach"))

    with pytest.raises(IntegrityError, match="uq_eg_source"):
        async with pg_session.begin_nested():
            await repo.add(_grant(user_id, source_ref="ticket-1"))

    assert (await repo.find_by_source_ref("support", "ticket-1")).id == first.id
    assert len(await repo.list_for_user(user_id, entitlement_key="standard")) == 3


async def test_grant_revoke_is_once_only(pg_session):
    repo = AsyncEntitlementGrantRepository(pg_session)
    user_id = await _user(pg_session)
    grant = await repo.add(_grant(user_id))

    revoked = await repo.revoke(
        grant.id, revoked_at=NOW, reason="mistake", revoked_by="ops@nutreeai.com"
    )
    again = await repo.revoke(
        grant.id,
        revoked_at=NOW + timedelta(1),
        reason="again",
        revoked_by="other@nutreeai.com",
    )

    assert revoked.revoked_at == NOW and again.revoke_reason == "mistake"
    assert again.revoked_by == "ops@nutreeai.com"
    assert (
        await repo.revoke(str(uuid4()), revoked_at=NOW, reason="x", revoked_by="a@b.c")
        is None
    )
    assert await repo.list_for_user(user_id, include_revoked=False) == []


# ---------------------------------------------------------------- projection


def _projection(user_id: str, **overrides) -> UserEntitlement:
    values = {
        "user_id": user_id,
        "entitlement_key": "standard",
        "has_access": True,
        "state": "active",
        "primary_source": "store",
        "version": 1,
        "computed_at": NOW,
        "access_until": NOW + timedelta(days=30),
        "next_change_at": NOW + timedelta(days=30),
        "ever_had_access": True,
        "last_provider_sync_at": NOW,
    }
    values.update(overrides)
    return UserEntitlement(**values)


async def test_projection_upsert_and_worker_queries(pg_session):
    repo = AsyncUserEntitlementRepository(pg_session)
    fresh, stale, lapsed = [await _user(pg_session) for _ in range(3)]
    deleted = await _user(pg_session, active=False)
    await repo.upsert(_projection(fresh))
    await repo.upsert(_projection(fresh, version=2, state="cancelled_active"))
    await repo.upsert(_projection(stale, last_provider_sync_at=NOW - timedelta(days=2)))
    # Stalest of all, but a deleted account is never re-synced.
    await repo.upsert(_projection(deleted, last_provider_sync_at=None))
    await repo.upsert(
        _projection(
            lapsed,
            has_access=False,
            state="expired",
            next_change_at=NOW - timedelta(minutes=1),
            last_provider_sync_at=None,
        )
    )

    stored = await repo.get(fresh)
    assert (stored.version, stored.state) == (2, "cancelled_active")
    assert len(await repo.list_for_user(fresh)) == 1
    assert await repo.list_due_for_recompute(now=NOW, limit=10) == [
        (lapsed, "standard")
    ]
    assert await repo.list_user_ids_for_resync(
        synced_before=NOW - timedelta(days=1), limit=10
    ) == [stale]

    await repo.set_last_purchase_hint_at(fresh, "standard", at=NOW)
    assert (await repo.get(fresh)).last_purchase_hint_at == NOW


async def test_projection_state_check(pg_session):
    repo = AsyncUserEntitlementRepository(pg_session)
    user_id = await _user(pg_session)

    with pytest.raises(IntegrityError, match="ck_ue_state"):
        async with pg_session.begin_nested():
            await repo.upsert(_projection(user_id, state="unknown"))


async def test_user_lock_blocks_another_transaction(async_session_factory):
    user_id = str(uuid4())
    try_lock = text("SELECT pg_try_advisory_xact_lock(hashtext('ent:' || :u))")
    async with async_session_factory() as holder, async_session_factory() as other:
        await AsyncUserEntitlementRepository(holder).lock_users([user_id, user_id])

        assert (await other.execute(try_lock, {"u": user_id})).scalar_one() is False
        await holder.commit()
        assert (await other.execute(try_lock, {"u": user_id})).scalar_one() is True
        await other.rollback()
