"""
Unit tests for Subscription model.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from src.domain.constants.lifetime_products import (
    LIFETIME_PRODUCT_ID,
    LIFETIME_UPGRADE_PRODUCT_ID,
    grants_access,
    is_lifetime_product,
    lifetime_base_id,
)
from src.infra.database.models.subscription import Subscription


class TestSubscriptionModel:
    """Test suite for Subscription model."""

    def test_subscription_is_active_when_status_active_and_not_expired(self):
        """Test that subscription is active when status is active and not expired."""
        now = datetime.now(timezone.utc)
        subscription = Subscription(
            id="sub_123",
            user_id="user_123",
            revenuecat_subscriber_id="rc_123",
            product_id="premium_monthly",
            platform="ios",
            status="active",
            purchased_at=now,
            expires_at=now + timedelta(days=30),
        )

        assert subscription.is_active() is True

    def test_subscription_not_active_when_expired(self):
        """Test that subscription is not active when expired."""
        now = datetime.now(timezone.utc)
        subscription = Subscription(
            id="sub_123",
            user_id="user_123",
            revenuecat_subscriber_id="rc_123",
            product_id="premium_monthly",
            platform="ios",
            status="active",
            purchased_at=now - timedelta(days=31),
            expires_at=now - timedelta(days=1),
        )

        assert subscription.is_active() is False

    def test_subscription_not_active_when_status_not_active(self):
        """Test that subscription is not active when status is not active."""
        now = datetime.now(timezone.utc)
        subscription = Subscription(
            id="sub_123",
            user_id="user_123",
            revenuecat_subscriber_id="rc_123",
            product_id="premium_monthly",
            platform="ios",
            status="cancelled",
            purchased_at=now,
            expires_at=now + timedelta(days=30),
        )

        assert subscription.is_active() is False

    def test_subscription_is_monthly(self):
        """Test monthly subscription detection."""
        subscription = Subscription(
            id="sub_123",
            user_id="user_123",
            revenuecat_subscriber_id="rc_123",
            product_id="premium_monthly",
            platform="ios",
            status="active",
            purchased_at=datetime.now(),
        )

        assert subscription.is_monthly() is True
        assert subscription.is_yearly() is False

    def test_subscription_is_yearly(self):
        """Test yearly subscription detection."""
        subscription = Subscription(
            id="sub_123",
            user_id="user_123",
            revenuecat_subscriber_id="rc_123",
            product_id="premium_yearly",
            platform="ios",
            status="active",
            purchased_at=datetime.now(),
        )

        assert subscription.is_yearly() is True
        assert subscription.is_monthly() is False

    def test_subscription_is_yearly_with_annual_keyword(self):
        """Test yearly subscription detection with 'annual' keyword."""
        subscription = Subscription(
            id="sub_123",
            user_id="user_123",
            revenuecat_subscriber_id="rc_123",
            product_id="premium_annual",
            platform="ios",
            status="active",
            purchased_at=datetime.now(),
        )

        assert subscription.is_yearly() is True
        assert subscription.is_monthly() is False

    def test_subscription_with_no_expiry_is_active(self):
        """Test that subscription with no expiry date is active if status is active."""
        subscription = Subscription(
            id="sub_123",
            user_id="user_123",
            revenuecat_subscriber_id="rc_123",
            product_id="premium_lifetime",
            platform="ios",
            status="active",
            purchased_at=datetime.now(),
            expires_at=None,  # Lifetime subscription
        )

        assert subscription.is_active() is True


def _row(product_id: str, **fields) -> Subscription:
    return Subscription(
        id="sub_123",
        user_id="user_123",
        revenuecat_subscriber_id="rc_123",
        product_id=product_id,
        platform="ios",
        status="active",
        purchased_at=datetime.now(UTC),
        **fields,
    )


class TestLifetimeSubscription:
    """A lifetime purchase is a known one-time product with no expiry."""

    @pytest.mark.parametrize(
        "product_id", [LIFETIME_PRODUCT_ID, LIFETIME_UPGRADE_PRODUCT_ID]
    )
    def test_a_lifetime_product_is_lifetime_and_no_plan_period(self, product_id):
        subscription = _row(product_id, expires_at=None)

        assert subscription.is_lifetime() is True
        assert subscription.is_active() is True
        assert subscription.is_monthly() is False
        assert subscription.is_yearly() is False

    @pytest.mark.parametrize(
        "product_id",
        ["premium_monthly", "discount_nutree_annually_0", "premium_lifetime", ""],
    )
    def test_any_other_product_is_not_lifetime(self, product_id):
        assert _row(product_id).is_lifetime() is False

    @pytest.mark.parametrize(
        ("product_id", "base"),
        [
            (LIFETIME_PRODUCT_ID, LIFETIME_PRODUCT_ID),
            # A Play base plan or purchase option suffix is dropped.
            (f"{LIFETIME_PRODUCT_ID}:buy", LIFETIME_PRODUCT_ID),
            (f"{LIFETIME_UPGRADE_PRODUCT_ID}:upgrade", LIFETIME_UPGRADE_PRODUCT_ID),
            ("discount_nutree_annually_0:annual", None),
            (None, None),
            (1299, None),
        ],
    )
    def test_the_lifetime_base_id_drops_a_play_suffix(self, product_id, base):
        assert lifetime_base_id(product_id) == base
        assert is_lifetime_product(product_id) is (base is not None)

    def test_a_refunded_lifetime_row_is_not_active(self):
        subscription = _row(LIFETIME_PRODUCT_ID, expires_at=None)
        subscription.status = "refunded"

        assert subscription.is_active() is False


class TestGrantsAccess:
    """The row rule the Premium gate's database check applies."""

    NOW = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)

    @pytest.mark.parametrize(
        ("status", "product_id", "expires_at", "granted"),
        [
            ("active", LIFETIME_PRODUCT_ID, None, True),
            ("billing_issue", LIFETIME_UPGRADE_PRODUCT_ID, None, True),
            ("refunded", LIFETIME_PRODUCT_ID, None, False),
            ("cancelled", LIFETIME_PRODUCT_ID, None, False),
            # A null expiry proves nothing for any other product.
            ("active", "premium_monthly", None, False),
            ("active", "premium_monthly", NOW + timedelta(days=1), True),
            ("active", "premium_monthly", NOW - timedelta(seconds=1), False),
            ("expired", "premium_monthly", NOW + timedelta(days=1), False),
        ],
    )
    def test_grants_access(self, status, product_id, expires_at, granted):
        assert grants_access(status, product_id, expires_at, self.NOW) is granted

    def test_a_naive_expiry_is_read_as_utc(self):
        naive = (self.NOW + timedelta(minutes=1)).replace(tzinfo=None)
        assert grants_access("active", "premium_monthly", naive, self.NOW) is True
