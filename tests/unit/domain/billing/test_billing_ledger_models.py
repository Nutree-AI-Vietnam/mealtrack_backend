"""Ledger records, grant rules and provider errors (NM-680)."""

from datetime import UTC, datetime, timedelta

import pytest

from src.domain.exceptions.billing_exceptions import (
    BillingProviderError,
    InvalidEntitlementGrant,
    InvalidProviderPayload,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderUnavailable,
)
from src.domain.model.billing import (
    ENTITLEMENT_FLAG_PREFIX,
    ENTITLEMENT_STANDARD,
    FLAG_ENT_LEDGER_WRITE,
    FLAG_ENT_READ_API,
    FLAG_ENT_READ_API_DEFER,
    FLAG_ENT_RECONCILE,
    GRANT_MAX_DAYS,
    NO_ACCESS_STORE_STATES,
    BillingCustomerIdentity,
    BillingProviderEvent,
    CustomerSnapshot,
    EntitlementGrant,
    EntitlementState,
    NormalizedSubscription,
    ProviderEntitlement,
    StoreSubscription,
    StoreSubscriptionState,
    validate_grant_request,
)

NOW = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
USER = "6f1d2c3b-4a59-4e8f-9d7c-1b2a3c4d5e6f"
OTHER_USER = "a1b2c3d4-0000-4000-8000-000000000002"
KEY = "rc:Xq7fK2mLp9RzT4vB8nWc3dHs6Ya1:nutree_2999_1y_0"


def _normalized(**overrides) -> NormalizedSubscription:
    values = {
        "subscription_key": KEY,
        "store": "app_store",
        "environment": "production",
        "source_customer_id": "Xq7fK2mLp9RzT4vB8nWc3dHs6Ya1",
        "product_id": "nutree_2999_1y_0",
        "entitlement_key": ENTITLEMENT_STANDARD,
        "state": "active",
        "period_type": "normal",
        "will_renew": True,
        "is_lifetime": False,
        "purchased_at": NOW - timedelta(days=30),
        "expires_at": NOW + timedelta(days=335),
        "grace_expires_at": None,
        "unsubscribe_detected_at": None,
        "billing_issue_detected_at": None,
        "refunded_at": None,
        "store_identifier": "2000000712345678",
        "ownership_type": "purchased",
    }
    values.update(overrides)
    return NormalizedSubscription(**values)


def _row(fetched_at=NOW) -> StoreSubscription:
    return StoreSubscription.from_snapshot(
        _normalized(),
        row_id="2f0b7a51-61c3-4b8e-a1f2-9d3c4b5a6e70",
        user_id=USER,
        provider="revenuecat",
        fetched_at=fetched_at,
        now=fetched_at,
    )


class TestStoreSubscription:
    def test_from_snapshot_copies_every_snapshot_field(self):
        row = _row()

        assert row.id == "2f0b7a51-61c3-4b8e-a1f2-9d3c4b5a6e70"
        assert row.user_id == USER
        assert row.provider == "revenuecat"
        assert row.subscription_key == KEY
        assert row.entitlement_key == ENTITLEMENT_STANDARD
        assert row.store_identifier == "2000000712345678"
        assert row.ownership_type == "purchased"
        assert row.version == 1
        assert row.fetched_at == NOW
        assert row.created_at == NOW
        assert row.absent_since is None

    def test_refresh_applies_newer_snapshot_and_can_move_owner(self):
        row = _row()
        later = NOW + timedelta(minutes=5)

        refreshed = row.refreshed_from(
            _normalized(will_renew=False, unsubscribe_detected_at=later),
            user_id=OTHER_USER,
            fetched_at=later,
            now=later,
        )

        assert refreshed.id == row.id
        assert refreshed.created_at == row.created_at
        assert refreshed.user_id == OTHER_USER
        assert refreshed.will_renew is False
        assert refreshed.version == 2
        assert refreshed.fetched_at == later
        assert refreshed.updated_at == later

    def test_older_snapshots_are_rejected(self):
        row = _row()

        assert row.accepts_snapshot(NOW)
        assert row.accepts_snapshot(NOW + timedelta(seconds=1))
        assert not row.accepts_snapshot(NOW - timedelta(seconds=1))

    def test_marked_absent_keeps_first_absence_time(self):
        first = NOW + timedelta(hours=1)
        second = NOW + timedelta(hours=2)

        absent = _row().marked_absent(fetched_at=first, now=first)
        again = absent.marked_absent(fetched_at=second, now=second)

        assert absent.state == StoreSubscriptionState.ABSENT
        assert absent.absent_since == first
        assert again.absent_since == first
        assert again.version == 3

    def test_refresh_clears_absence(self):
        later = NOW + timedelta(hours=3)
        absent = _row().marked_absent(fetched_at=NOW, now=NOW)

        refreshed = absent.refreshed_from(
            _normalized(), user_id=USER, fetched_at=later, now=later
        )

        assert refreshed.state == "active"
        assert refreshed.absent_since is None


class TestEntitlementGrant:
    def _grant(self, **overrides) -> EntitlementGrant:
        values = {
            "id": "g-1",
            "user_id": USER,
            "entitlement_key": ENTITLEMENT_STANDARD,
            "source": "qa",
            "reason": "TestFlight review account",
            "starts_at": NOW,
            "ends_at": NOW + timedelta(days=7),
            "created_by": "qa@nutreeai.com",
        }
        values.update(overrides)
        return EntitlementGrant(**values)

    def test_live_window_is_half_open(self):
        grant = self._grant()

        assert not grant.is_live_at(NOW - timedelta(seconds=1))
        assert grant.is_live_at(NOW)
        assert grant.is_live_at(grant.ends_at - timedelta(seconds=1))
        assert not grant.is_live_at(grant.ends_at)

    def test_revoked_grant_is_never_live(self):
        revoked = self._grant().revoked(
            at=NOW + timedelta(hours=1), reason="Mistake", by="ops@nutreeai.com"
        )

        assert revoked.is_revoked
        assert revoked.revoke_reason == "Mistake"
        assert revoked.revoked_by == "ops@nutreeai.com"
        assert not revoked.is_live_at(NOW)


class TestValidateGrantRequest:
    def _validate(self, **overrides):
        values = {
            "source": "support",
            "reason": "Refund dispute goodwill",
            "starts_at": NOW,
            "ends_at": NOW + timedelta(days=30),
        }
        values.update(overrides)
        validate_grant_request(**values)

    def test_valid_request(self):
        self._validate()

    def test_maximum_window_is_allowed(self):
        self._validate(ends_at=NOW + timedelta(days=GRANT_MAX_DAYS))

    @pytest.mark.parametrize(
        "overrides",
        [
            {"source": "marketing"},
            {"reason": ""},
            {"reason": "   "},
            {"reason": "x" * 256},
            {"ends_at": NOW},
            {"ends_at": NOW - timedelta(days=1)},
            {"ends_at": NOW + timedelta(days=GRANT_MAX_DAYS, seconds=1)},
            {"starts_at": NOW.replace(tzinfo=None)},
        ],
    )
    def test_invalid_requests(self, overrides):
        with pytest.raises(InvalidEntitlementGrant):
            self._validate(**overrides)

    def test_unknown_start_checks_only_what_it_can(self):
        self._validate(starts_at=None, ends_at=NOW - timedelta(days=500))
        with pytest.raises(InvalidEntitlementGrant):
            self._validate(starts_at=None, ends_at=NOW.replace(tzinfo=None))

    def test_end_is_bounded_from_now_so_grants_cannot_be_chained(self):
        self._validate(now=NOW, ends_at=NOW + timedelta(days=GRANT_MAX_DAYS))
        # A few minutes of admin clock skew are tolerated.
        self._validate(
            now=NOW,
            starts_at=NOW + timedelta(minutes=5),
            ends_at=NOW + timedelta(days=GRANT_MAX_DAYS, minutes=5),
        )
        with pytest.raises(InvalidEntitlementGrant, match="from now"):
            self._validate(
                now=NOW,
                starts_at=NOW + timedelta(days=GRANT_MAX_DAYS),
                ends_at=NOW + timedelta(days=GRANT_MAX_DAYS + 1),
            )
        with pytest.raises(InvalidEntitlementGrant, match="from now"):
            self._validate(
                now=NOW - timedelta(days=1),
                ends_at=NOW + timedelta(days=GRANT_MAX_DAYS),
            )


class TestOtherRecords:
    def test_provider_event_settled_states(self):
        def event(status):
            return BillingProviderEvent(
                id="e-1",
                provider="revenuecat",
                provider_event_id="A1B2C3D4-0000-4000-8000-000000000001",
                event_type="RENEWAL",
                status=status,
                received_at=NOW,
            )

        assert event("processed").is_settled
        assert event("ignored").is_settled
        for status in ("received", "processing", "retry", "unmatched", "dead"):
            assert not event(status).is_settled

    def test_identity_retired(self):
        identity = BillingCustomerIdentity(
            id="i-1",
            user_id=USER,
            provider="revenuecat",
            provider_customer_id="Xq7fK2mLp9RzT4vB8nWc3dHs6Ya1",
            kind="primary",
            first_seen_at=NOW,
            last_seen_at=NOW,
        )

        assert identity.project == "default"
        assert not identity.is_retired

    def test_snapshot_helpers(self):
        snapshot = CustomerSnapshot(
            provider="revenuecat",
            canonical_customer_id="$RCAnonymousID:abc",
            aliases=["uid-1", "$RCAnonymousID:abc", "uid-1"],
            subscriptions=[],
            fetched_at=NOW,
            entitlements={
                "standard": ProviderEntitlement(
                    entitlement_key="standard",
                    product_id="p",
                    purchased_at=NOW - timedelta(days=1),
                    expires_at=None,
                    grace_expires_at=None,
                )
            },
        )

        assert snapshot.customer_ids() == ["$RCAnonymousID:abc", "uid-1"]
        assert snapshot.provider_reports_active(now=NOW)


class TestConstants:
    def test_string_enums_compare_equal_to_stored_values(self):
        assert StoreSubscriptionState.IN_GRACE == "in_grace"
        assert EntitlementState.CANCELLED_ACTIVE == "cancelled_active"
        assert ENTITLEMENT_STANDARD == "standard"

    def test_no_access_states(self):
        assert NO_ACCESS_STORE_STATES == {"paused", "refunded", "revoked", "absent"}

    def test_server_only_flags_share_the_filtered_prefix(self):
        for flag in (
            FLAG_ENT_LEDGER_WRITE,
            FLAG_ENT_RECONCILE,
            FLAG_ENT_READ_API,
            FLAG_ENT_READ_API_DEFER,
        ):
            assert flag.startswith(ENTITLEMENT_FLAG_PREFIX)


class TestProviderErrors:
    @pytest.mark.parametrize(
        ("error_cls", "code"),
        [
            (ProviderUnavailable, "provider_unavailable"),
            (ProviderRateLimited, "provider_rate_limited"),
            (ProviderNotFound, "provider_not_found"),
            (InvalidProviderPayload, "provider_invalid_payload"),
        ],
    )
    def test_codes_and_hierarchy(self, error_cls, code):
        error = error_cls("boom", provider="revenuecat")

        assert isinstance(error, BillingProviderError)
        assert error.code == code
        assert error.provider == "revenuecat"
        assert str(error) == "boom"

    def test_rate_limited_carries_retry_after(self):
        error = ProviderRateLimited(retry_after_seconds=2.5)

        assert error.retry_after_seconds == 2.5
        assert str(error) == "provider_rate_limited"

    def test_grant_error_is_a_value_error(self):
        assert issubclass(InvalidEntitlementGrant, ValueError)
