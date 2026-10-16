"""RevenueCat v1 ``GET /subscribers`` body -> CustomerSnapshot (NM-680)."""

import copy
from datetime import UTC, datetime, timedelta

import pytest

from src.domain.exceptions.billing_exceptions import InvalidProviderPayload
from src.domain.model.billing import (
    ENTITLEMENT_STANDARD,
    PROVIDER_REVENUECAT,
    NormalizedSubscription,
)
from src.domain.services.billing.revenuecat_normalizer import (
    derive_subscription_state,
    normalize_revenuecat_subscriber,
    revenuecat_subscription_key,
)

NOW = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
UID = "Xq7fK2mLp9RzT4vB8nWc3dHs6Ya1"
ANNUAL = "nutree_2999_1y_0"
MONTHLY = "nutree_999_1m_0"


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _normalize(body, *, fetched_at=NOW, requested=UID):
    return normalize_revenuecat_subscriber(
        body, fetched_at=fetched_at, requested_customer_id=requested
    )


def _only(snapshot) -> NormalizedSubscription:
    assert len(snapshot.subscriptions) == 1
    return snapshot.subscriptions[0]


def _by_product(snapshot) -> dict[str, NormalizedSubscription]:
    return {sub.product_id: sub for sub in snapshot.subscriptions}


class TestSnapshotEnvelope:
    def test_snapshot_identity_and_metadata(self, rc_body):
        snapshot = _normalize(rc_body("active_play"))

        assert snapshot.provider == PROVIDER_REVENUECAT
        assert snapshot.canonical_customer_id == UID
        assert snapshot.requested_customer_id == UID
        assert snapshot.aliases == []
        assert snapshot.fetched_at == NOW
        assert snapshot.customer_ids() == [UID]

    def test_anonymous_original_id_is_canonical_and_requested_id_is_alias(
        self, rc_body
    ):
        anonymous = "$RCAnonymousID:8b1f2c3d4e5f60718293a4b5c6d7e8f9"

        snapshot = _normalize(rc_body("anonymous_original"))

        assert snapshot.canonical_customer_id == anonymous
        assert snapshot.aliases == [UID]
        assert snapshot.customer_ids() == [anonymous, UID]
        sub = _only(snapshot)
        assert sub.source_customer_id == anonymous
        assert sub.subscription_key == f"rc:{anonymous}:{ANNUAL}"

    def test_never_subscribed_customer_has_no_rows(self, rc_body):
        snapshot = _normalize(rc_body("never_subscribed"))

        assert snapshot.subscriptions == []
        assert snapshot.entitlements == {}
        assert not snapshot.provider_reports_active(ENTITLEMENT_STANDARD, now=NOW)

    def test_subscriber_attributes_do_not_affect_the_snapshot(self, rc_body):
        body = rc_body("trial")
        stripped = copy.deepcopy(body)
        del stripped["subscriber"]["subscriber_attributes"]

        assert _normalize(body) == _normalize(stripped)

    def test_naive_fetched_at_is_treated_as_utc(self, rc_body):
        snapshot = _normalize(rc_body("trial"), fetched_at=NOW.replace(tzinfo=None))

        assert snapshot.fetched_at == NOW


class TestSubscriptionKey:
    def test_v1_key_is_canonical_customer_plus_product(self):
        assert revenuecat_subscription_key(UID, ANNUAL) == f"rc:{UID}:{ANNUAL}"

    def test_key_is_stable_across_fetches(self, rc_body):
        first = _normalize(rc_body("multiple_entitlements"))
        later = _normalize(
            rc_body("multiple_entitlements"), fetched_at=NOW + timedelta(days=3)
        )

        assert sorted(s.subscription_key for s in first.subscriptions) == sorted(
            s.subscription_key for s in later.subscriptions
        )

    def test_two_customers_with_the_same_product_do_not_collide(self, rc_body):
        other = rc_body("active_play")
        other["subscriber"]["original_app_user_id"] = "OtherFirebaseUid0000000000001"

        mine = _only(_normalize(rc_body("active_play")))
        theirs = _only(_normalize(other, requested="OtherFirebaseUid0000000000001"))

        assert mine.product_id == theirs.product_id
        assert mine.subscription_key != theirs.subscription_key


class TestSubscriptionStates:
    def test_trial(self, rc_body):
        sub = _only(_normalize(rc_body("trial")))

        assert sub.subscription_key == f"rc:{UID}:{ANNUAL}"
        assert sub.state == "trialing"
        assert sub.period_type == "trial"
        assert sub.entitlement_key == ENTITLEMENT_STANDARD
        assert sub.store == "app_store"
        assert sub.environment == "production"
        assert sub.will_renew is True
        assert sub.is_lifetime is False
        assert sub.purchased_at == _ts("2026-10-12T09:30:00+00:00")
        assert sub.expires_at == _ts("2026-10-19T09:30:00+00:00")
        assert sub.ownership_type == "purchased"
        assert sub.store_identifier == "2000000712345678"

    def test_active_play_subscription_keyed_by_product_not_base_plan(self, rc_body):
        sub = _only(_normalize(rc_body("active_play")))

        assert sub.product_id == MONTHLY
        assert sub.subscription_key == f"rc:{UID}:{MONTHLY}"
        assert sub.store == "play_store"
        assert sub.state == "active"
        assert sub.period_type == "normal"
        assert sub.will_renew is True
        assert sub.store_identifier == "GPA.3345-6789-0123-45678..5"

    def test_cancelled_in_period_stays_active_but_will_not_renew(self, rc_body):
        sub = _only(_normalize(rc_body("cancelled_active")))

        assert sub.state == "active"
        assert sub.will_renew is False
        assert sub.unsubscribe_detected_at == _ts("2026-10-01T18:22:10+00:00")
        assert sub.expires_at == _ts("2026-11-01T00:00:00+00:00")

    def test_grace_period_after_expiry(self, rc_body):
        sub = _only(_normalize(rc_body("grace")))

        assert sub.state == "in_grace"
        assert sub.billing_issue_detected_at == _ts("2026-10-14T00:05:31+00:00")
        assert sub.grace_expires_at == _ts("2026-10-30T00:00:00+00:00")
        assert sub.will_renew is False

    def test_billing_issue_while_period_is_still_open_is_grace(self, rc_body):
        # RevenueCat can extend expires_date to the end of the store grace period.
        body = rc_body("grace")
        entry = body["subscriber"]["subscriptions"][ANNUAL]
        entry["expires_date"] = "2026-10-30T00:00:00Z"
        entry["grace_period_expires_date"] = None

        sub = _only(_normalize(body))

        assert sub.state == "in_grace"

    def test_grace_falls_back_to_entitlement_grace_date(self, rc_body):
        body = rc_body("grace")
        body["subscriber"]["subscriptions"][ANNUAL]["grace_period_expires_date"] = None

        sub = _only(_normalize(body))

        assert sub.grace_expires_at == _ts("2026-10-30T00:00:00+00:00")
        assert sub.state == "in_grace"

    def test_billing_retry_after_grace_ended(self, rc_body):
        sub = _only(_normalize(rc_body("billing_retry")))

        assert sub.state == "billing_retry"
        assert sub.grace_expires_at == _ts("2026-10-06T00:00:00+00:00")

    def test_paused_play_subscription_after_period_end(self, rc_body):
        sub = _only(_normalize(rc_body("paused")))

        assert sub.state == "paused"
        assert sub.store == "play_store"

    def test_scheduled_pause_keeps_the_current_period_active(self, rc_body):
        before_pause = datetime(2026, 10, 5, tzinfo=UTC)

        sub = _only(_normalize(rc_body("paused"), fetched_at=before_pause))

        assert sub.state == "active"

    def test_refunded(self, rc_body):
        sub = _only(_normalize(rc_body("refunded")))

        assert sub.state == "refunded"
        assert sub.refunded_at == _ts("2026-10-05T11:42:00+00:00")
        assert sub.will_renew is False

    def test_refund_of_an_earlier_period_does_not_mark_a_newer_purchase(self, rc_body):
        body = rc_body("refunded")
        entry = body["subscriber"]["subscriptions"][ANNUAL]
        entry["purchase_date"] = "2026-10-06T09:00:00Z"
        entry["expires_date"] = "2027-10-06T09:00:00Z"
        entry["unsubscribe_detected_at"] = None

        sub = _only(_normalize(body))

        assert sub.state == "active"

    def test_expired(self, rc_body):
        sub = _only(_normalize(rc_body("expired")))

        assert sub.state == "expired"
        assert sub.will_renew is False

    def test_family_shared_purchase_counts(self, rc_body):
        sub = _only(_normalize(rc_body("family_shared")))

        assert sub.ownership_type == "family_shared"
        assert sub.entitlement_key == ENTITLEMENT_STANDARD
        assert sub.state == "active"

    def test_sandbox_purchase_is_marked_sandbox(self, rc_body):
        sub = _only(_normalize(rc_body("sandbox")))

        assert sub.environment == "sandbox"
        assert sub.state == "active"


class TestLifetimeAndNonSubscriptions:
    def test_lifetime_non_subscription_referenced_by_entitlement(self, rc_body):
        snapshot = _normalize(rc_body("lifetime"))

        sub = _only(snapshot)  # the unreferenced tip-jar purchase is skipped
        assert sub.product_id == "nutree_lifetime"
        assert sub.subscription_key == f"rc:{UID}:nutree_lifetime"
        assert sub.is_lifetime is True
        assert sub.expires_at is None
        assert sub.state == "active"
        assert sub.will_renew is False
        assert sub.entitlement_key == ENTITLEMENT_STANDARD
        assert sub.store == "app_store"
        assert sub.purchased_at == _ts("2026-06-01T10:00:00+00:00")
        assert sub.store_identifier == "2000000598765432"

    def test_non_renewing_purchase_uses_entitlement_expiry(self, rc_body):
        body = rc_body("lifetime")
        body["subscriber"]["entitlements"]["standard"]["expires_date"] = (
            "2026-12-01T10:00:00Z"
        )

        sub = _only(_normalize(body))

        assert sub.is_lifetime is False
        assert sub.expires_at == _ts("2026-12-01T10:00:00+00:00")
        assert sub.state == "active"

    def test_latest_of_several_purchases_is_used(self, rc_body):
        body = rc_body("lifetime")
        purchases = body["subscriber"]["non_subscriptions"]["nutree_lifetime"]
        purchases.insert(
            0,
            {
                "id": "older0001",
                "is_sandbox": True,
                "purchase_date": "2026-01-01T00:00:00Z",
                "store": "play_store",
                "store_transaction_id": "GPA.older",
            },
        )

        sub = _only(_normalize(body))

        assert sub.store_identifier == "2000000598765432"
        assert sub.environment == "production"

    def test_empty_purchase_list_falls_back_to_promotional_row(self, rc_body):
        body = rc_body("lifetime")
        body["subscriber"]["non_subscriptions"]["nutree_lifetime"] = []

        snapshot = _normalize(body)

        # The entitlement is still active, so it surfaces as a promotional row.
        sub = _only(snapshot)
        assert sub.store == "promotional"


class TestPromotional:
    def test_active_entitlement_without_purchase_becomes_promotional_row(self, rc_body):
        sub = _only(_normalize(rc_body("promotional_temporary")))

        assert sub.store == "promotional"
        assert sub.product_id == "rc_promo_standard_monthly"
        assert sub.subscription_key == f"rc:{UID}:rc_promo_standard_monthly"
        assert sub.entitlement_key == ENTITLEMENT_STANDARD
        assert sub.environment == "production"
        assert sub.period_type == "promotional"
        assert sub.state == "active"
        assert sub.will_renew is False
        assert sub.expires_at == _ts("2026-11-10T00:00:00+00:00")

    def test_expired_entitlement_without_purchase_is_not_synthesized(self, rc_body):
        body = rc_body("promotional_temporary")
        body["subscriber"]["entitlements"]["standard"]["expires_date"] = (
            "2026-10-01T00:00:00Z"
        )

        assert _normalize(body).subscriptions == []

    def test_lifetime_promotional_grant_listed_as_subscription(self, rc_body):
        sub = _only(_normalize(rc_body("promotional_listed")))

        assert sub.store == "promotional"
        assert sub.is_lifetime is True
        assert sub.state == "active"
        assert sub.will_renew is False
        assert sub.entitlement_key == ENTITLEMENT_STANDARD


class TestMultipleEntitlements:
    def test_each_product_maps_to_its_entitlement(self, rc_body):
        snapshot = _normalize(rc_body("multiple_entitlements"))
        rows = _by_product(snapshot)

        assert set(rows) == {ANNUAL, MONTHLY, "nutree_coach_1m"}
        # The annual product backs both "pro" and "standard"; the row keeps standard.
        assert rows[ANNUAL].entitlement_key == ENTITLEMENT_STANDARD
        assert rows["nutree_coach_1m"].entitlement_key == "coach"
        # The upgraded-from monthly backs no entitlement any more.
        assert rows[MONTHLY].entitlement_key is None
        assert rows[MONTHLY].state == "expired"

    def test_provider_entitlement_view_is_kept_for_shadow_comparison(self, rc_body):
        snapshot = _normalize(rc_body("multiple_entitlements"))

        assert set(snapshot.entitlements) == {"coach", "pro", "standard"}
        assert snapshot.provider_reports_active("standard", now=NOW)
        assert snapshot.provider_reports_active("pro", now=NOW)
        assert not snapshot.provider_reports_active("missing", now=NOW)
        assert snapshot.entitlements["standard"].product_id == ANNUAL

    def test_provider_view_follows_sdk_semantics_for_grace(self, rc_body):
        # The SDKs read only entitlements[k].expires_date.
        snapshot = _normalize(rc_body("grace"))

        assert not snapshot.provider_reports_active("standard", now=NOW)


class TestFieldNormalization:
    @pytest.mark.parametrize(
        ("raw_store", "expected"),
        [
            ("app_store", "app_store"),
            ("mac_app_store", "app_store"),
            ("play_store", "play_store"),
            ("stripe", "stripe"),
            ("rc_billing", "rc_billing"),
            ("amazon", "amazon"),
            ("promotional", "promotional"),
            ("paddle", "other"),
            (None, "other"),
        ],
    )
    def test_store_values(self, rc_body, raw_store, expected):
        body = rc_body("active_play")
        body["subscriber"]["subscriptions"][MONTHLY]["store"] = raw_store

        assert _only(_normalize(body)).store == expected

    def test_unknown_period_and_ownership_types_become_none(self, rc_body):
        body = rc_body("active_play")
        entry = body["subscriber"]["subscriptions"][MONTHLY]
        entry["period_type"] = "mystery"
        entry["ownership_type"] = "UNKNOWN"

        sub = _only(_normalize(body))

        assert sub.period_type is None
        assert sub.ownership_type is None

    def test_missing_is_sandbox_means_production(self, rc_body):
        body = rc_body("active_play")
        del body["subscriber"]["subscriptions"][MONTHLY]["is_sandbox"]

        assert _only(_normalize(body)).environment == "production"

    def test_millisecond_epoch_timestamps_are_accepted(self, rc_body):
        body = rc_body("active_play")
        expires = datetime(2026, 11, 1, 12, tzinfo=UTC)
        body["subscriber"]["subscriptions"][MONTHLY]["expires_date"] = int(
            expires.timestamp() * 1000
        )

        assert _only(_normalize(body)).expires_at == expires

    def test_fractional_seconds_and_offsets_are_normalized_to_utc(self, rc_body):
        body = rc_body("active_play")
        body["subscriber"]["subscriptions"][MONTHLY]["expires_date"] = (
            "2026-11-01T14:00:00.250+02:00"
        )

        assert _only(_normalize(body)).expires_at == datetime(
            2026, 11, 1, 12, 0, 0, 250000, tzinfo=UTC
        )


class TestMalformedPayloads:
    def test_missing_subscriber(self):
        with pytest.raises(InvalidProviderPayload) as exc:
            _normalize({"request_date": "2026-10-15T08:00:00Z"})
        assert exc.value.provider == PROVIDER_REVENUECAT
        assert exc.value.code == "provider_invalid_payload"

    def test_missing_original_app_user_id(self, rc_body):
        body = rc_body("trial")
        del body["subscriber"]["original_app_user_id"]

        with pytest.raises(InvalidProviderPayload):
            _normalize(body)

    def test_subscriptions_not_an_object(self, rc_body):
        body = rc_body("trial")
        body["subscriber"]["subscriptions"] = []

        with pytest.raises(InvalidProviderPayload):
            _normalize(body)

    def test_subscription_entry_not_an_object(self, rc_body):
        body = rc_body("trial")
        body["subscriber"]["subscriptions"][ANNUAL] = "active"

        with pytest.raises(InvalidProviderPayload):
            _normalize(body)

    def test_unparseable_expiry_never_reads_as_lifetime(self, rc_body):
        body = rc_body("trial")
        body["subscriber"]["subscriptions"][ANNUAL]["expires_date"] = "next tuesday"

        with pytest.raises(InvalidProviderPayload):
            _normalize(body)

    def test_missing_expiry_field_never_reads_as_lifetime(self, rc_body):
        body = rc_body("trial")
        del body["subscriber"]["subscriptions"][ANNUAL]["expires_date"]

        with pytest.raises(InvalidProviderPayload):
            _normalize(body)

    def test_boolean_timestamp_is_rejected(self, rc_body):
        body = rc_body("trial")
        body["subscriber"]["subscriptions"][ANNUAL]["expires_date"] = True

        with pytest.raises(InvalidProviderPayload):
            _normalize(body)

    def test_non_subscription_purchases_not_a_list(self, rc_body):
        body = rc_body("lifetime")
        body["subscriber"]["non_subscriptions"]["nutree_lifetime"] = {"id": "x"}

        with pytest.raises(InvalidProviderPayload):
            _normalize(body)

    def test_entitlement_not_an_object(self, rc_body):
        body = rc_body("trial")
        body["subscriber"]["entitlements"]["standard"] = None

        with pytest.raises(InvalidProviderPayload):
            _normalize(body)

    def test_non_subscription_purchase_not_an_object(self, rc_body):
        body = rc_body("lifetime")
        body["subscriber"]["non_subscriptions"]["nutree_lifetime"].append("bad")

        with pytest.raises(InvalidProviderPayload):
            _normalize(body)

    def test_missing_optional_maps_are_empty(self, rc_body):
        body = rc_body("promotional_temporary")
        for name in ("subscriptions", "non_subscriptions", "other_purchases"):
            del body["subscriber"][name]

        assert _only(_normalize(body)).store == "promotional"


class TestEdgeMappings:
    def test_entitlement_without_product_identifier_maps_nothing(self, rc_body):
        body = rc_body("trial")
        del body["subscriber"]["entitlements"]["standard"]["product_identifier"]

        sub = _only(_normalize(body))

        assert sub.entitlement_key is None

    def test_subscription_row_wins_over_same_product_purchase(self, rc_body):
        body = rc_body("trial")
        body["subscriber"]["non_subscriptions"][ANNUAL] = [
            {
                "id": "dup1",
                "purchase_date": "2026-10-12T09:30:00Z",
                "store": "app_store",
            }
        ]

        sub = _only(_normalize(body))

        assert sub.state == "trialing"
        assert sub.is_lifetime is False

    def test_unlisted_product_backing_two_entitlements_yields_one_row(self, rc_body):
        body = rc_body("promotional_temporary")
        entitlements = body["subscriber"]["entitlements"]
        entitlements["pro"] = dict(entitlements["standard"])

        sub = _only(_normalize(body))

        assert sub.entitlement_key == ENTITLEMENT_STANDARD

    def test_lifetime_promotional_entitlement_without_purchase(self, rc_body):
        body = rc_body("promotional_temporary")
        body["subscriber"]["entitlements"]["standard"]["expires_date"] = None

        sub = _only(_normalize(body))

        assert sub.is_lifetime is True
        assert sub.state == "active"

    def test_newest_purchase_wins_even_when_listed_first(self, rc_body):
        body = rc_body("lifetime")
        purchases = body["subscriber"]["non_subscriptions"]["nutree_lifetime"]
        purchases.append(
            {
                "id": "older0002",
                "purchase_date": "2026-01-01T00:00:00Z",
                "store": "play_store",
                "store_transaction_id": "GPA.older",
            }
        )

        sub = _only(_normalize(body))

        assert sub.store == "app_store"
        assert sub.store_identifier == "2000000598765432"


class TestDeriveSubscriptionState:
    PAST = NOW - timedelta(days=1)
    FUTURE = NOW + timedelta(days=1)

    def _state(self, **overrides):
        values = {
            "now": NOW,
            "expires_at": self.FUTURE,
            "grace_expires_at": None,
            "billing_issue_detected_at": None,
            "refunded_at": None,
            "purchased_at": NOW - timedelta(days=30),
            "period_type": "normal",
            "auto_resume_at": None,
            "is_lifetime": False,
        }
        values.update(overrides)
        return derive_subscription_state(**values)

    def test_active(self):
        assert self._state() == "active"

    def test_trialing(self):
        assert self._state(period_type="trial") == "trialing"

    def test_refund_wins_over_everything(self):
        assert self._state(refunded_at=self.PAST, is_lifetime=True) == "refunded"

    def test_lifetime(self):
        assert self._state(expires_at=None, is_lifetime=True) == "active"

    def test_paused(self):
        assert self._state(expires_at=self.PAST, auto_resume_at=self.FUTURE) == (
            "paused"
        )

    def test_billing_issue_inside_open_period_is_grace(self):
        assert self._state(billing_issue_detected_at=self.PAST) == "in_grace"

    def test_grace_window_after_expiry(self):
        assert (
            self._state(
                expires_at=self.PAST,
                grace_expires_at=self.FUTURE,
                billing_issue_detected_at=self.PAST,
            )
            == "in_grace"
        )

    def test_billing_retry_after_grace(self):
        assert (
            self._state(
                expires_at=self.PAST - timedelta(days=16),
                grace_expires_at=self.PAST,
                billing_issue_detected_at=self.PAST - timedelta(days=16),
            )
            == "billing_retry"
        )

    def test_expiry_instant_counts_as_over(self):
        assert self._state(expires_at=NOW) == "expired"

    def test_expired(self):
        assert self._state(expires_at=self.PAST) == "expired"
