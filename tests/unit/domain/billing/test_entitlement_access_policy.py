"""The NM-680 access rule, projection recompute and read-time checks."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from src.domain.model.billing import (
    ENTITLEMENT_STANDARD,
    EntitlementGrant,
    NormalizedSubscription,
    StoreSubscription,
    UserEntitlement,
)
from src.domain.services.billing.access_policy import (
    compute_projection,
    effective_has_access,
    needs_recompute,
    parse_user_id_allowlist,
    sandbox_access_allowed,
    store_access_end,
    store_subscription_grants_access,
)
from src.domain.services.billing.revenuecat_normalizer import (
    normalize_revenuecat_subscriber,
)

NOW = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
USER = "6f1d2c3b-4a59-4e8f-9d7c-1b2a3c4d5e6f"
UID = "Xq7fK2mLp9RzT4vB8nWc3dHs6Ya1"
ANNUAL = "nutree_2999_1y_0"


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _project(subs=(), grants=(), *, now=NOW, sandbox_allowed=True, **kwargs):
    return compute_projection(
        USER,
        ENTITLEMENT_STANDARD,
        list(subs),
        list(grants),
        now,
        sandbox_allowed,
        **kwargs,
    )


def _from_fixture(
    rc_body, name, *, now=NOW, sandbox_allowed=True, key=ENTITLEMENT_STANDARD
):
    snapshot = normalize_revenuecat_subscriber(
        rc_body(name), fetched_at=now, requested_customer_id=UID
    )
    return compute_projection(
        USER, key, snapshot.subscriptions, [], now, sandbox_allowed
    )


def _sub(**overrides) -> NormalizedSubscription:
    values = {
        "subscription_key": f"rc:{UID}:{ANNUAL}",
        "store": "app_store",
        "environment": "production",
        "source_customer_id": UID,
        "product_id": ANNUAL,
        "entitlement_key": ENTITLEMENT_STANDARD,
        "state": "active",
        "period_type": "normal",
        "will_renew": True,
        "is_lifetime": False,
        "purchased_at": NOW - timedelta(days=20),
        "expires_at": NOW + timedelta(days=10),
        "grace_expires_at": None,
        "unsubscribe_detected_at": None,
        "billing_issue_detected_at": None,
        "refunded_at": None,
    }
    values.update(overrides)
    return NormalizedSubscription(**values)


def _grant(**overrides) -> EntitlementGrant:
    values = {
        "id": "0b6c4c2e-0d1f-4bb0-9b1e-3c2f7a8d9e10",
        "user_id": USER,
        "entitlement_key": ENTITLEMENT_STANDARD,
        "source": "support",
        "reason": "Store outage compensation",
        "starts_at": NOW - timedelta(days=1),
        "ends_at": NOW + timedelta(days=30),
        "created_by": "support@nutreeai.com",
    }
    values.update(overrides)
    return EntitlementGrant(**values)


class TestFixtureProjections:
    def test_trial(self, rc_body):
        projection = _from_fixture(rc_body, "trial")
        expires = _ts("2026-10-19T09:30:00+00:00")

        assert projection.has_access is True
        assert projection.state == "trial"
        assert projection.access_until == expires
        assert projection.next_change_at == expires
        assert projection.period_type == "trial"
        assert projection.will_renew is True
        assert projection.primary_source == "store"
        assert projection.primary_ref == f"rc:{UID}:{ANNUAL}"
        assert projection.product_id == ANNUAL
        assert projection.store == "app_store"
        assert projection.is_sandbox is False
        assert projection.ever_had_access is True
        assert projection.version == 1
        assert projection.computed_at == NOW

    def test_active(self, rc_body):
        projection = _from_fixture(rc_body, "active_play")

        assert projection.has_access is True
        assert projection.state == "active"
        assert projection.store == "play_store"
        assert projection.access_until == _ts("2026-11-01T12:00:00+00:00")

    def test_cancelled_in_period_keeps_access_until_expiry(self, rc_body):
        projection = _from_fixture(rc_body, "cancelled_active")

        assert projection.has_access is True
        assert projection.state == "cancelled_active"
        assert projection.will_renew is False
        assert projection.access_until == _ts("2026-11-01T00:00:00+00:00")

    def test_grace_grants_access_until_grace_end(self, rc_body):
        projection = _from_fixture(rc_body, "grace")
        grace_end = _ts("2026-10-30T00:00:00+00:00")

        assert projection.has_access is True
        assert projection.state == "grace"
        assert projection.access_until == grace_end
        assert projection.next_change_at == grace_end

    def test_billing_retry_after_grace_has_no_access(self, rc_body):
        projection = _from_fixture(rc_body, "billing_retry")

        assert projection.has_access is False
        assert projection.state == "expired"
        assert projection.access_until == _ts("2026-10-06T00:00:00+00:00")
        assert projection.next_change_at is None
        assert projection.will_renew is False

    def test_paused_has_no_access(self, rc_body):
        projection = _from_fixture(rc_body, "paused")

        assert projection.has_access is False
        assert projection.state == "expired"
        assert projection.ever_had_access is True

    def test_refunded_has_no_access(self, rc_body):
        projection = _from_fixture(rc_body, "refunded")

        assert projection.has_access is False
        assert projection.state == "expired"
        assert projection.ever_had_access is True

    def test_lifetime(self, rc_body):
        projection = _from_fixture(rc_body, "lifetime")

        assert projection.has_access is True
        assert projection.state == "active"
        assert projection.is_lifetime is True
        assert projection.access_until is None
        assert projection.next_change_at is None
        assert projection.will_renew is False
        assert effective_has_access(projection, NOW + timedelta(days=3650))

    def test_sandbox_when_policy_allows(self, rc_body):
        projection = _from_fixture(rc_body, "sandbox", sandbox_allowed=True)

        assert projection.has_access is True
        assert projection.is_sandbox is True

    def test_sandbox_when_policy_denies(self, rc_body):
        projection = _from_fixture(rc_body, "sandbox", sandbox_allowed=False)

        assert projection.has_access is False
        assert projection.state == "none"
        assert projection.ever_had_access is False
        assert projection.product_id is None
        assert projection.next_change_at is None

    def test_promotional_entitlement_without_subscription(self, rc_body):
        projection = _from_fixture(rc_body, "promotional_temporary")

        assert projection.has_access is True
        assert projection.state == "active"
        assert projection.store == "promotional"
        assert projection.will_renew is False
        assert projection.access_until == _ts("2026-11-10T00:00:00+00:00")

    def test_multiple_entitlements_project_independently(self, rc_body):
        standard = _from_fixture(rc_body, "multiple_entitlements")
        coach = _from_fixture(rc_body, "multiple_entitlements", key="coach")

        assert standard.has_access is True
        assert standard.product_id == ANNUAL
        assert standard.access_until == _ts("2027-09-15T00:00:00+00:00")
        assert coach.has_access is True
        assert coach.product_id == "nutree_coach_1m"
        assert coach.access_until == _ts("2026-11-02T00:00:00+00:00")

    def test_expired(self, rc_body):
        projection = _from_fixture(rc_body, "expired")

        assert projection.has_access is False
        assert projection.state == "expired"
        assert projection.access_until == _ts("2026-09-01T00:00:00+00:00")
        assert projection.product_id == "nutree_999_1m_0"

    def test_family_shared_grants_access(self, rc_body):
        assert _from_fixture(rc_body, "family_shared").has_access is True

    def test_never_subscribed(self, rc_body):
        projection = _from_fixture(rc_body, "never_subscribed")

        assert projection.has_access is False
        assert projection.state == "none"
        assert projection.ever_had_access is False
        assert projection.primary_source == "store"
        assert projection.primary_ref is None
        assert projection.access_until is None
        assert projection.will_renew is None

    def test_ledger_rows_and_snapshot_rows_project_identically(self, rc_body):
        snapshot = normalize_revenuecat_subscriber(
            rc_body("cancelled_active"), fetched_at=NOW, requested_customer_id=UID
        )
        rows = [
            StoreSubscription.from_snapshot(
                sub,
                row_id=f"row-{index}",
                user_id=USER,
                provider=snapshot.provider,
                fetched_at=snapshot.fetched_at,
                now=NOW,
            )
            for index, sub in enumerate(snapshot.subscriptions)
        ]

        assert _project(rows) == _project(snapshot.subscriptions)


class TestStoreRule:
    @pytest.mark.parametrize("state", ["paused", "refunded", "revoked", "absent"])
    def test_excluded_states_never_grant_access(self, state):
        sub = _sub(state=state, is_lifetime=True, expires_at=None)

        projection = _project([sub])

        assert projection.has_access is False
        assert projection.state == "expired"
        assert projection.next_change_at is None

    def test_absent_row_after_transfer_leaves_no_access(self):
        projection = _project([_sub(state="absent")])

        assert projection.has_access is False
        assert projection.ever_had_access is True

    def test_flags_never_remove_access(self):
        sub = _sub(
            will_renew=False,
            unsubscribe_detected_at=NOW - timedelta(days=2),
            billing_issue_detected_at=NOW - timedelta(hours=1),
        )

        projection = _project([sub])

        assert projection.has_access is True
        assert projection.state == "grace"

    def test_stale_state_label_does_not_extend_access(self):
        # Stored as "active" at fetch time, but its dates have passed since.
        sub = _sub(expires_at=NOW - timedelta(minutes=1))

        assert _project([sub]).has_access is False

    def test_grace_boundaries(self):
        expires = NOW - timedelta(hours=1)
        grace_end = NOW + timedelta(days=5)
        sub = _sub(state="in_grace", expires_at=expires, grace_expires_at=grace_end)

        at_expiry = _project([sub], now=expires)
        in_grace = _project([sub])
        at_grace_end = _project([sub], now=grace_end)

        assert at_expiry.has_access is True and at_expiry.state == "grace"
        assert at_expiry.next_change_at == grace_end
        assert in_grace.has_access is True and in_grace.state == "grace"
        assert at_grace_end.has_access is False

    def test_active_with_grace_beyond_expiry_changes_state_at_expiry(self):
        expires = NOW + timedelta(days=1)
        grace_end = NOW + timedelta(days=17)
        sub = _sub(expires_at=expires, grace_expires_at=grace_end)

        projection = _project([sub])

        assert projection.state == "active"
        assert projection.access_until == grace_end
        assert projection.next_change_at == expires

    def test_other_entitlement_keys_are_ignored(self):
        assert _project([_sub(entitlement_key="coach")]).has_access is False
        assert _project([_sub(entitlement_key=None)]).has_access is False

    def test_renewing_subscription_is_primary_over_cancelled_one(self):
        cancelled = _sub(
            subscription_key=f"rc:{UID}:old_annual",
            product_id="old_annual",
            will_renew=False,
            unsubscribe_detected_at=NOW - timedelta(days=3),
            expires_at=NOW + timedelta(days=200),
        )
        renewing = _sub(expires_at=NOW + timedelta(days=20))

        projection = _project([cancelled, renewing])

        assert projection.state == "active"
        assert projection.product_id == ANNUAL
        assert projection.will_renew is True
        assert projection.access_until == NOW + timedelta(days=200)
        assert projection.next_change_at == NOW + timedelta(days=20)

    def test_lifetime_wins_primary_and_coverage(self):
        lifetime = _sub(
            subscription_key=f"rc:{UID}:nutree_lifetime",
            product_id="nutree_lifetime",
            is_lifetime=True,
            expires_at=None,
            will_renew=False,
        )

        projection = _project([_sub(), lifetime])

        assert projection.is_lifetime is True
        assert projection.access_until is None
        assert projection.product_id == "nutree_lifetime"

    def test_store_access_end(self):
        assert store_access_end(_sub(is_lifetime=True, expires_at=None)) is None
        assert store_access_end(_sub(grace_expires_at=None)) == NOW + timedelta(days=10)
        assert store_access_end(
            _sub(grace_expires_at=NOW + timedelta(days=12))
        ) == NOW + timedelta(days=12)
        assert store_access_end(
            _sub(expires_at=None, grace_expires_at=NOW + timedelta(days=2))
        ) == NOW + timedelta(days=2)

    def test_store_subscription_grants_access(self):
        sandbox = _sub(environment="sandbox")

        assert store_subscription_grants_access(_sub(), NOW, sandbox_allowed=False)
        assert not store_subscription_grants_access(sandbox, NOW, sandbox_allowed=False)
        assert store_subscription_grants_access(sandbox, NOW, sandbox_allowed=True)
        assert not store_subscription_grants_access(
            _sub(environment="staging"), NOW, sandbox_allowed=True
        )
        assert not store_subscription_grants_access(
            _sub(state="refunded"), NOW, sandbox_allowed=True
        )
        assert store_subscription_grants_access(
            _sub(is_lifetime=True, expires_at=None), NOW, sandbox_allowed=False
        )


class TestGrants:
    def test_grant_only_access(self):
        grant = _grant()

        projection = _project(grants=[grant])

        assert projection.has_access is True
        assert projection.state == "active"
        assert projection.primary_source == "grant"
        assert projection.primary_ref == grant.id
        assert projection.will_renew is False
        assert projection.product_id is None
        assert projection.store is None
        assert projection.access_until == grant.ends_at
        assert projection.next_change_at == grant.ends_at
        assert projection.ever_had_access is True

    def test_revoked_grant_gives_no_access(self):
        grant = _grant(
            revoked_at=NOW - timedelta(hours=2), revoke_reason="Granted to wrong user"
        )

        projection = _project(grants=[grant])

        assert projection.has_access is False
        assert projection.state == "expired"  # it was live before the revoke
        assert projection.next_change_at is None

    def test_grant_revoked_before_it_started_never_counted(self):
        grant = _grant(
            starts_at=NOW + timedelta(days=1),
            revoked_at=NOW - timedelta(hours=1),
        )

        projection = _project(grants=[grant])

        assert projection.state == "none"
        assert projection.ever_had_access is False

    def test_future_grant_sets_next_change_at_its_start(self):
        grant = _grant(
            starts_at=NOW + timedelta(days=2), ends_at=NOW + timedelta(days=9)
        )

        projection = _project(grants=[grant])

        assert projection.has_access is False
        assert projection.state == "none"
        assert projection.next_change_at == grant.starts_at
        later = _project(grants=[grant], now=grant.starts_at)
        assert later.has_access is True
        assert later.access_until == grant.ends_at

    def test_lapsed_grant_is_described(self):
        grant = _grant(
            starts_at=NOW - timedelta(days=40), ends_at=NOW - timedelta(days=10)
        )

        projection = _project(grants=[grant])

        assert projection.state == "expired"
        assert projection.primary_source == "grant"
        assert projection.access_until == grant.ends_at

    def test_store_subscription_is_primary_over_grant(self):
        projection = _project([_sub()], [_grant()])

        assert projection.primary_source == "store"
        assert projection.product_id == ANNUAL
        # Coverage chains through the grant, which outlasts the subscription.
        assert projection.access_until == NOW + timedelta(days=30)
        assert projection.next_change_at == NOW + timedelta(days=10)

    def test_grant_starting_at_store_expiry_extends_access(self):
        expires = NOW + timedelta(days=10)
        grant = _grant(starts_at=expires, ends_at=expires + timedelta(days=30))

        projection = _project([_sub(expires_at=expires)], [grant])

        assert projection.access_until == grant.ends_at
        assert projection.next_change_at == expires

    def test_gap_between_store_and_grant_is_not_bridged(self):
        expires = NOW + timedelta(days=10)
        grant = _grant(
            starts_at=expires + timedelta(days=1), ends_at=expires + timedelta(days=30)
        )

        projection = _project([_sub(expires_at=expires)], [grant])

        assert projection.access_until == expires
        assert projection.next_change_at == expires

    def test_other_entitlement_grants_are_ignored(self):
        assert _project(grants=[_grant(entitlement_key="coach")]).has_access is False


class TestNextChangeAt:
    def test_earliest_future_boundary_across_sources(self):
        subs = [
            _sub(
                expires_at=NOW + timedelta(days=3),
                grace_expires_at=NOW + timedelta(days=19),
            ),
            _sub(
                subscription_key=f"rc:{UID}:other",
                product_id="other",
                expires_at=NOW - timedelta(days=1),
            ),
        ]
        grants = [
            _grant(starts_at=NOW + timedelta(days=5), ends_at=NOW + timedelta(days=6)),
        ]

        assert _project(subs, grants).next_change_at == NOW + timedelta(days=3)

    def test_ignores_excluded_and_ineligible_rows(self):
        subs = [
            _sub(state="refunded", expires_at=NOW + timedelta(days=1)),
            _sub(environment="sandbox", expires_at=NOW + timedelta(days=2)),
            _sub(expires_at=NOW + timedelta(days=4)),
        ]

        projection = _project(subs, sandbox_allowed=False)

        assert projection.next_change_at == NOW + timedelta(days=4)

    def test_revoked_grant_boundaries_are_ignored(self):
        grant = _grant(revoked_at=NOW - timedelta(minutes=5))

        assert _project([_sub()], [grant]).next_change_at == NOW + timedelta(days=10)


class TestPreviousProjection:
    def _previous(self, **overrides) -> UserEntitlement:
        values = {
            "user_id": USER,
            "entitlement_key": ENTITLEMENT_STANDARD,
            "has_access": True,
            "state": "active",
            "primary_source": "store",
            "version": 41,
            "computed_at": NOW - timedelta(days=1),
            "ever_had_access": True,
            "last_provider_sync_at": NOW - timedelta(hours=3),
            "last_purchase_hint_at": NOW - timedelta(hours=4),
        }
        values.update(overrides)
        return UserEntitlement(**values)

    def test_version_bumps_and_timestamps_carry_over(self):
        previous = self._previous()

        projection = _project([_sub()], previous=previous)

        assert projection.version == 42
        assert projection.last_provider_sync_at == previous.last_provider_sync_at
        assert projection.last_purchase_hint_at == previous.last_purchase_hint_at

    def test_provider_sync_time_overrides(self):
        synced = NOW - timedelta(seconds=2)

        projection = _project(
            [_sub()], previous=self._previous(), provider_synced_at=synced
        )

        assert projection.last_provider_sync_at == synced

    def test_ever_had_access_is_sticky(self):
        # e.g. the purchase was transferred away and its row now belongs to another user
        projection = _project([], previous=self._previous())

        assert projection.has_access is False
        assert projection.state == "expired"
        assert projection.ever_had_access is True


class TestReadTime:
    def test_effective_has_access_after_expiry_with_stale_projection(self, rc_body):
        projection = _from_fixture(rc_body, "trial")
        after_expiry = projection.access_until + timedelta(seconds=1)

        assert effective_has_access(projection, NOW) is True
        assert needs_recompute(projection, NOW) is False
        # Nobody recomputed the row: the cached "yes" must not survive expiry.
        assert projection.has_access is True
        assert effective_has_access(projection, after_expiry) is False
        assert needs_recompute(projection, after_expiry) is True

        snapshot = normalize_revenuecat_subscriber(
            rc_body("trial"), fetched_at=NOW, requested_customer_id=UID
        )
        recomputed = compute_projection(
            USER,
            ENTITLEMENT_STANDARD,
            snapshot.subscriptions,
            [],
            after_expiry,
            True,
            previous=projection,
        )
        assert recomputed.has_access is False
        assert recomputed.state == "expired"
        assert recomputed.version == projection.version + 1
        assert needs_recompute(recomputed, after_expiry) is False

    def test_grace_projection_survives_until_grace_end(self, rc_body):
        projection = _from_fixture(rc_body, "grace")
        grace_end = _ts("2026-10-30T00:00:00+00:00")

        assert effective_has_access(projection, grace_end - timedelta(seconds=1))
        assert not effective_has_access(projection, grace_end)

    def test_no_access_projection_stays_no(self, rc_body):
        projection = _from_fixture(rc_body, "expired")

        assert effective_has_access(projection, NOW) is False

    def test_non_lifetime_access_without_end_fails_closed(self):
        projection = UserEntitlement(
            user_id=USER,
            entitlement_key=ENTITLEMENT_STANDARD,
            has_access=True,
            state="active",
            primary_source="store",
            version=1,
            computed_at=NOW,
        )

        assert effective_has_access(projection, NOW) is False
        assert needs_recompute(projection, NOW) is False

    def test_naive_datetimes_are_treated_as_utc(self):
        sub = _sub(expires_at=(NOW + timedelta(days=1)).replace(tzinfo=None))

        projection = _project([sub], now=NOW.replace(tzinfo=None))

        assert projection.has_access is True
        assert effective_has_access(
            replace(
                projection, access_until=projection.access_until.replace(tzinfo=None)
            ),
            NOW,
        )


class TestSandboxPolicy:
    @pytest.mark.parametrize(
        ("policy", "user_id", "expected"),
        [
            ("all", USER, True),
            ("ALL", USER, True),
            (None, USER, True),
            ("", USER, True),
            ("unexpected", USER, True),
            ("none", USER, False),
            (" None ", USER, False),
            ("allowlist", USER, True),
            ("allowlist", "someone-else", False),
        ],
    )
    def test_sandbox_access_allowed(self, policy, user_id, expected):
        assert sandbox_access_allowed(policy, user_id, frozenset({USER})) is expected

    def test_parse_user_id_allowlist(self):
        assert parse_user_id_allowlist(None) == frozenset()
        assert parse_user_id_allowlist("") == frozenset()
        assert parse_user_id_allowlist(" a , b,,c ") == frozenset({"a", "b", "c"})
