"""
Unit tests for RevenueCat webhook handler.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.v1.webhook_subscription_lifecycle import (
    _notify_affiliate,
    _publish_subscription_event,
)
from src.api.routes.v1.webhooks import (
    _credit_referral_on_purchase,
    _revoke_referral_on_refund,
    find_user_for_revenuecat_event,
    handle_billing_issue,
    handle_cancellation,
    handle_expiration,
    handle_product_change,
    handle_purchase,
    handle_refund,
    handle_renewal,
    handle_transfer,
    parse_platform,
    parse_timestamp,
    revenuecat_webhook,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.referral.referral_conversion import ReferralConversion
from src.infra.database.models.referral.referral_wallet import ReferralWallet


class _FakeReferralRepo:
    """In-memory referral repo performing REAL wallet arithmetic (not mocked away).

    Mirrors the exact balance/total math of ReferralRepository.credit_wallet /
    revoke_from_wallet, minus the SQL session — so `_credit_referral_on_purchase`
    and `_revoke_referral_on_refund` exercise their real money-movement logic.
    """

    def __init__(self, conversion=None, wallet=None):
        self.conversion = conversion
        self.wallet = wallet or ReferralWallet(
            user_id=conversion.referrer_user_id if conversion else "referrer_1",
            balance=0,
            total_earned=0,
            total_revoked=0,
            total_withdrawn=0,
            updated_at=utc_now(),
        )

    async def get_conversion_by_referred_user(self, user_id, for_update=False):
        return self.conversion

    async def credit_wallet(self, user_id, amount):
        self.wallet.balance += amount
        self.wallet.total_earned += amount
        self.wallet.updated_at = utc_now()
        return self.wallet

    async def revoke_from_wallet(self, user_id, amount):
        self.wallet.balance = max(0, self.wallet.balance - amount)
        self.wallet.total_revoked += amount
        self.wallet.updated_at = utc_now()
        return self.wallet


@pytest.mark.asyncio
class TestWebhookHelpers:
    """Test webhook helper functions."""

    def test_parse_platform(self):
        """Test platform parsing from store name."""
        assert parse_platform("APP_STORE") == "ios"
        assert parse_platform("PLAY_STORE") == "android"
        assert parse_platform("PADDLE") == "web"
        assert parse_platform("STRIPE") == "web"
        assert parse_platform("MAC_APP_STORE") == "ios"
        assert parse_platform(None) == "ios"
        assert parse_platform("") == "ios"
        assert parse_platform("UNKNOWN") == "ios"

    def test_parse_timestamp(self):
        """Test timestamp parsing from milliseconds."""
        # Valid timestamp
        ms = 1696800000000  # Oct 8, 2023
        result = parse_timestamp(ms)
        assert isinstance(result, datetime)
        assert result.year == 2023

        # None timestamp
        assert parse_timestamp(None) is None

    async def test_subscription_event_uses_versioned_envelope(self):
        publisher = MagicMock()
        publisher.publish = AsyncMock()

        await _publish_subscription_event(
            publisher,
            "subscription_renewal",
            {"mealtrack_user_id": "user_123"},
            "revenuecat-event-1",
            "user_123",
        )

        payload = publisher.publish.await_args.args[0]
        assert payload["event_type"] == "subscription.lifecycle.v1"
        assert payload["aggregate_type"] == "subscription"
        assert payload["aggregate_id"] == "user_123"
        assert payload["data"]["lifecycle_type"] == "subscription_renewal"

    async def test_subscription_queue_failure_propagates_for_provider_retry(self):
        publisher = MagicMock()
        publisher.publish = AsyncMock(side_effect=RuntimeError("Queue unavailable"))

        with pytest.raises(RuntimeError, match="Queue unavailable"):
            await _notify_affiliate(
                None,
                "subscription_renewal",
                {},
                "revenuecat-event-1",
                event_publisher=publisher,
                user_id="user_123",
            )

        # Zero timestamp
        assert parse_timestamp(0) is not None

        # Invalid timestamp
        with patch("src.api.routes.v1.webhooks.logger"):
            result = parse_timestamp("invalid")
            assert result is None


@pytest.mark.asyncio
class TestWebhookHandler:
    """Test webhook handler functions."""

    @pytest.fixture
    def mock_request(self):
        """Create mock request object."""
        request = MagicMock()
        request.json = AsyncMock()
        return request

    @pytest.fixture
    def mock_event_publisher(self):
        """Provide a configured Queue publisher for webhook tests."""
        publisher = MagicMock()
        publisher.publish = AsyncMock()
        return publisher

    @pytest.fixture
    def mock_uow(self):
        """Create mock Unit of Work."""
        uow = AsyncMock()
        uow.users = AsyncMock()
        uow.session = MagicMock()
        uow.commit = AsyncMock()
        uow.rollback = AsyncMock()
        return uow

    @pytest.fixture
    def webhook_event(self):
        """Create sample webhook event."""
        return {
            "event": {
                "type": "INITIAL_PURCHASE",
                "app_user_id": "user_123",
                "product_id": "premium_monthly",
                "store": "APP_STORE",
                "environment": "PRODUCTION",
                "purchased_at_ms": 1696800000000,
                "expiration_at_ms": 1699478400000,
                "transaction_id": "1000000123456789",
            }
        }

    async def test_webhook_success(
        self, mock_request, webhook_event, mock_event_publisher
    ):
        """Test successful webhook processing."""
        mock_request.json.return_value = webhook_event

        # Set a valid webhook secret for the test
        with patch("src.api.routes.v1.webhooks.os.getenv", return_value="test_secret"):
            with (
                patch(
                    "src.api.routes.v1.webhooks._get_event_publisher",
                    return_value=mock_event_publisher,
                ),
                patch("src.api.routes.v1.webhooks.AsyncUnitOfWork") as mock_uow_class,
            ):
                mock_uow = MagicMock()
                mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
                mock_uow.__aexit__ = AsyncMock(return_value=False)
                mock_uow.commit = AsyncMock()
                mock_uow.rollback = AsyncMock()
                mock_uow.referrals.get_conversion_by_referred_user = AsyncMock(
                    return_value=None
                )
                mock_uow_class.return_value = mock_uow

                # Mock user exists
                mock_user = MagicMock(id="user_123")
                mock_uow.session.execute = AsyncMock()
                mock_result = MagicMock()
                mock_result.scalars.return_value.first.return_value = mock_user
                mock_uow.session.execute.return_value = mock_result

                # Mock no existing subscription (async)
                with patch(
                    "src.api.routes.v1.webhook_subscription_lifecycle.get_subscription_by_revenuecat_id",
                    new_callable=AsyncMock,
                    return_value=None,
                ):
                    result = await revenuecat_webhook(
                        mock_request, authorization="test_secret"
                    )

                assert result == {"status": "success"}
                # commit/rollback are owned by the AsyncUnitOfWork context manager, not called explicitly
                mock_uow.commit.assert_not_awaited()

    async def test_web_lead_webhook_reconciles_authoritative_subscriber_immediately(
        self, mock_request
    ):
        lead_id = "11111111-1111-1111-1111-111111111111"
        mock_request.json.return_value = {
            "event": {
                "id": "provider-1",
                "type": "INITIAL_PURCHASE",
                "app_user_id": lead_id.upper(),
            }
        }
        with patch("src.api.routes.v1.webhooks.os.getenv", return_value="test_secret"):
            with patch("src.api.routes.v1.webhooks.AsyncUnitOfWork") as uow_class:
                uow = MagicMock()
                uow.__aenter__ = AsyncMock(return_value=uow)
                uow.__aexit__ = AsyncMock(return_value=False)
                uow_class.return_value = uow
                subscription_service = MagicMock()
                subscriber = {"subscriber": {"entitlements": {"standard": {}}}}
                subscription_service.get_subscriber_info = AsyncMock(
                    return_value=subscriber
                )
                with (
                    patch(
                        "src.api.routes.v1.webhooks.reconcile_revenuecat_event",
                        new_callable=AsyncMock,
                        return_value=True,
                    ) as reconcile,
                    patch(
                        "src.api.routes.v1.webhooks._get_subscription_service",
                        return_value=subscription_service,
                    ),
                    patch(
                        "src.api.routes.v1.webhooks.get_web_funnel_outbox_dispatcher",
                        return_value=AsyncMock(),
                    ) as get_dispatcher,
                ):
                    result = await revenuecat_webhook(
                        mock_request, authorization="test_secret"
                    )

        assert result == {"status": "success"}
        subscription_service.get_subscriber_info.assert_awaited_once_with(lead_id)
        assert reconcile.await_args.args[2] == subscriber
        get_dispatcher.return_value.assert_awaited_once_with(lead_id=lead_id)

    async def test_webhook_user_not_found_redacts_provider_ids(
        self, mock_request, webhook_event, caplog
    ):
        """Test webhook returns 404 when user not found (so RevenueCat retries)."""
        webhook_event["event"]["aliases"] = ["$RCAnonymousID:alias_secret"]
        webhook_event["event"]["original_app_user_id"] = "original_secret"
        mock_request.json.return_value = webhook_event

        # Set a valid webhook secret for the test
        with patch("src.api.routes.v1.webhooks.os.getenv", return_value="test_secret"):
            with patch("src.api.routes.v1.webhooks.AsyncUnitOfWork") as mock_uow_class:
                mock_uow = MagicMock()
                mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
                mock_uow.__aexit__ = AsyncMock(return_value=False)
                mock_uow.commit = AsyncMock()
                mock_uow.rollback = AsyncMock()
                mock_uow.subscriptions = MagicMock()
                mock_uow.subscriptions.find_by_revenuecat_id = AsyncMock(
                    return_value=None
                )
                mock_uow_class.return_value = mock_uow

                # Mock user not found for all lookup strategies
                mock_uow.session.execute = AsyncMock()
                mock_result = MagicMock()
                mock_result.scalars.return_value.first.return_value = None
                mock_uow.session.execute.return_value = mock_result

                # Mock subscriptions repository (fallback lookup path)
                mock_uow.subscriptions = MagicMock()
                mock_uow.subscriptions.find_by_revenuecat_id = AsyncMock(
                    return_value=None
                )

                with caplog.at_level("ERROR"):
                    with pytest.raises(HTTPException) as exc_info:
                        await revenuecat_webhook(
                            mock_request, authorization="test_secret"
                        )

                assert exc_info.value.status_code == 404
                assert "user_123" not in caplog.text
                assert "$RCAnonymousID:alias_secret" not in caplog.text
                assert "original_secret" not in caplog.text
                assert "premium_monthly" not in caplog.text

    async def test_webhook_lifecycle_user_not_found_is_ignored(self, mock_request):
        """Test userless lifecycle events ACK to stop RevenueCat retry storms."""
        mock_request.json.return_value = {
            "event": {
                "type": "BILLING_ISSUE",
                "app_user_id": "$RCAnonymousID:missing",
                "product_id": "nutree_2999_1y_0",
            }
        }

        with patch("src.api.routes.v1.webhooks.os.getenv", return_value="test_secret"):
            with patch("src.api.routes.v1.webhooks.AsyncUnitOfWork") as mock_uow_class:
                mock_uow = MagicMock()
                mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
                mock_uow.__aexit__ = AsyncMock(return_value=False)
                mock_uow.subscriptions = MagicMock()
                mock_uow.subscriptions.find_by_revenuecat_id = AsyncMock(
                    return_value=None
                )
                mock_uow.session.execute = AsyncMock()
                mock_result = MagicMock()
                mock_result.scalars.return_value.first.return_value = None
                mock_uow.session.execute.return_value = mock_result
                mock_uow_class.return_value = mock_uow

                result = await revenuecat_webhook(
                    mock_request, authorization="test_secret"
                )

                assert result == {"status": "ignored", "reason": "user_not_found"}

    async def test_webhook_anonymous_purchase_is_acknowledged(self, mock_request):
        """Anonymous checkout events must not retry before Firebase redemption."""
        mock_request.json.return_value = {
            "event": {
                "type": "INITIAL_PURCHASE",
                "app_user_id": "$RCAnonymousID:checkout",
            }
        }

        with patch("src.api.routes.v1.webhooks.os.getenv", return_value="test_secret"):
            with patch("src.api.routes.v1.webhooks.AsyncUnitOfWork") as mock_uow_class:
                mock_uow = MagicMock()
                mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
                mock_uow.__aexit__ = AsyncMock(return_value=False)
                mock_uow.subscriptions = MagicMock()
                mock_uow.subscriptions.find_by_revenuecat_id = AsyncMock(
                    return_value=None
                )
                mock_result = MagicMock()
                mock_result.scalars.return_value.first.return_value = None
                mock_uow.session.execute = AsyncMock(return_value=mock_result)
                mock_uow_class.return_value = mock_uow

                result = await revenuecat_webhook(
                    mock_request, authorization="test_secret"
                )

        assert result == {"status": "ignored", "reason": "user_not_found"}

    async def test_find_user_for_revenuecat_event_matches_uuid_string_user_id(self):
        """UUID-shaped RevenueCat IDs must be compared against string User.id values."""
        user_id = "1d599ac9-1f3f-4697-b11f-92e30584bb2b"
        mock_user = MagicMock(id=user_id)
        mock_uow = MagicMock()
        mock_uow.session.execute = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.side_effect = [None, mock_user]
        mock_uow.session.execute.return_value = mock_result

        result = await find_user_for_revenuecat_event(
            mock_uow,
            {"app_user_id": user_id},
        )

        assert result is mock_user
        id_lookup = mock_uow.session.execute.await_args_list[1].args[0]
        assert id_lookup.compile().params["id_1"] == user_id

    async def test_webhook_transfer_without_target_user_is_acknowledged(
        self, mock_request
    ):
        """Anonymous transfers ACK when no Firebase target exists yet."""
        mock_request.json.return_value = {
            "event": {
                "type": "TRANSFER",
                "transferred_from": ["$RCAnonymousID:old"],
                "transferred_to": ["$RCAnonymousID:new"],
            }
        }

        with patch("src.api.routes.v1.webhooks.os.getenv", return_value="test_secret"):
            with patch("src.api.routes.v1.webhooks.AsyncUnitOfWork") as mock_uow_class:
                mock_uow = MagicMock()
                mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
                mock_uow.__aexit__ = AsyncMock(return_value=False)
                mock_uow.subscriptions = MagicMock()
                mock_uow.subscriptions.find_by_revenuecat_id = AsyncMock(
                    return_value=None
                )
                mock_result = MagicMock()
                mock_result.scalars.return_value.first.return_value = None
                mock_uow.session.execute = AsyncMock(return_value=mock_result)
                mock_uow_class.return_value = mock_uow

                result = await revenuecat_webhook(
                    mock_request, authorization="test_secret"
                )

                assert result == {"status": "ignored", "reason": "user_not_found"}

    async def test_webhook_transfer_syncs_target_user_from_revenuecat(
        self, mock_request
    ):
        """A transfer to a Firebase UID creates its RevenueCat-backed cache row."""
        mock_request.json.return_value = {
            "event": {
                "type": "TRANSFER",
                "transferred_from": ["$RCAnonymousID:checkout"],
                "transferred_to": ["firebase_uid_123"],
                "store": "STRIPE",
                "environment": "PRODUCTION",
            }
        }
        target_user = MagicMock(id="user_123", firebase_uid="firebase_uid_123")
        mock_service = MagicMock()
        mock_service.get_subscription_info = AsyncMock(
            return_value={
                "product_id": "premium_monthly",
                "expires_date": datetime(2026, 8, 31),
                "store": "STRIPE",
            }
        )

        with patch("src.api.routes.v1.webhooks.os.getenv", return_value="test_secret"):
            with patch("src.api.routes.v1.webhooks.AsyncUnitOfWork") as mock_uow_class:
                mock_uow = MagicMock()
                mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
                mock_uow.__aexit__ = AsyncMock(return_value=False)
                mock_uow.subscriptions = MagicMock()
                mock_uow.subscriptions.find_by_revenuecat_id = AsyncMock(
                    side_effect=[None, None, None]
                )
                no_user = MagicMock()
                no_user.scalars.return_value.first.return_value = None
                found_user = MagicMock()
                found_user.scalars.return_value.first.return_value = target_user
                mock_uow.session.execute = AsyncMock(side_effect=[no_user, found_user])
                mock_uow_class.return_value = mock_uow

                with (
                    patch(
                        "src.api.routes.v1.webhook_referral_funnel._get_subscription_service",
                        return_value=mock_service,
                    ),
                    patch(
                        "src.api.routes.v1.webhook_referral_funnel._lock_subscription_cache",
                        new_callable=AsyncMock,
                    ),
                ):
                    result = await revenuecat_webhook(
                        mock_request, authorization="test_secret"
                    )

        assert result == {"status": "success"}
        mock_service.get_subscription_info.assert_awaited_once_with("firebase_uid_123")
        subscription = mock_uow.session.add.call_args.args[0]
        assert subscription.user_id == "user_123"
        assert subscription.revenuecat_subscriber_id == "firebase_uid_123"
        assert subscription.product_id == "premium_monthly"

    async def test_webhook_purchase_redemption_syncs_redeemer_cache(self, mock_request):
        """A Paddle redemption refreshes the Firebase user's RevenueCat cache."""
        mock_request.json.return_value = {
            "event": {
                "type": "PURCHASE_REDEEMED",
                "redeemed_by": ["firebase_uid_123"],
                "store": "PADDLE",
                "environment": "PRODUCTION",
            }
        }
        target_user = MagicMock(id="user_123", firebase_uid="firebase_uid_123")
        mock_service = MagicMock()
        mock_service.get_subscription_info = AsyncMock(
            return_value={
                "product_id": "premium_monthly",
                "expires_date": datetime(2026, 8, 31),
                "store": "PADDLE",
            }
        )

        with patch("src.api.routes.v1.webhooks.os.getenv", return_value="test_secret"):
            with patch("src.api.routes.v1.webhooks.AsyncUnitOfWork") as mock_uow_class:
                mock_uow = MagicMock()
                mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
                mock_uow.__aexit__ = AsyncMock(return_value=False)
                mock_uow.subscriptions = MagicMock()
                mock_uow.subscriptions.find_by_revenuecat_id = AsyncMock(
                    return_value=None
                )
                found_user = MagicMock()
                found_user.scalars.return_value.first.return_value = target_user
                mock_uow.session.execute = AsyncMock(return_value=found_user)
                mock_uow_class.return_value = mock_uow

                with (
                    patch(
                        "src.api.routes.v1.webhook_referral_funnel._get_subscription_service",
                        return_value=mock_service,
                    ),
                    patch(
                        "src.api.routes.v1.webhook_referral_funnel._lock_subscription_cache",
                        new_callable=AsyncMock,
                    ) as lock_cache,
                ):
                    result = await revenuecat_webhook(
                        mock_request, authorization="test_secret"
                    )

        assert result == {"status": "success"}
        mock_service.get_subscription_info.assert_awaited_once_with("firebase_uid_123")
        lock_cache.assert_awaited_once_with(mock_uow, "firebase_uid_123")
        subscription = mock_uow.session.add.call_args.args[0]
        assert subscription.user_id == "user_123"
        assert subscription.revenuecat_subscriber_id == "firebase_uid_123"
        assert subscription.platform == "web"

    async def test_webhook_invalid_json(self, mock_request):
        """Test webhook with invalid JSON."""
        mock_request.json.side_effect = Exception("Invalid JSON")

        # Set a valid webhook secret for the test
        with patch("src.api.routes.v1.webhooks.os.getenv", return_value="test_secret"):
            with pytest.raises(HTTPException) as exc_info:
                await revenuecat_webhook(mock_request, authorization="test_secret")

            assert exc_info.value.status_code == 400
            assert exc_info.value.detail == "Invalid JSON"

    async def test_webhook_authorization_check(self, mock_request, webhook_event):
        """Test webhook authorization check."""
        mock_request.json.return_value = webhook_event

        with patch("src.api.routes.v1.webhooks.os.getenv") as mock_getenv:
            mock_getenv.return_value = "secret_token"

            # Test with wrong authorization
            with pytest.raises(HTTPException) as exc_info:
                await revenuecat_webhook(mock_request, authorization="wrong_token")

            assert exc_info.value.status_code == 401
            assert exc_info.value.detail in (
                "Unauthorized",
                "Invalid authorization header",
            )

    async def test_handle_purchase(self, mock_uow):
        """Test handling initial purchase event."""
        user = MagicMock(id="user_123")
        event = {
            "app_user_id": "user_123",
            "product_id": "premium_monthly",
            "store": "APP_STORE",
            "purchased_at_ms": 1696800000000,
            "expiration_at_ms": 1699478400000,
            "transaction_id": "123456",
            "environment": "PRODUCTION",
        }

        # Mock no existing subscription (async) and referral credit side effect
        with (
            patch(
                "src.api.routes.v1.webhook_subscription_lifecycle.get_subscription_by_revenuecat_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "src.api.routes.v1.webhook_subscription_lifecycle.credit_referral_on_purchase",
                new_callable=AsyncMock,
            ),
        ):
            await handle_purchase(
                mock_uow, user, event, event_publisher=MagicMock(publish=AsyncMock())
            )

        # Verify subscription was added
        mock_uow.session.add.assert_called_once()
        added_subscription = mock_uow.session.add.call_args[0][0]
        assert added_subscription.user_id == "user_123"
        assert added_subscription.product_id == "premium_monthly"
        assert added_subscription.status == "active"

    async def test_handle_renewal(self, mock_uow):
        """Test handling renewal event."""
        user = MagicMock(id="user_123")
        subscription = MagicMock()
        event = {"app_user_id": "user_123", "expiration_at_ms": 1699478400000}

        # Mock existing subscription (async)
        with patch(
            "src.api.routes.v1.webhook_subscription_lifecycle.get_subscription_by_revenuecat_id",
            new_callable=AsyncMock,
            return_value=subscription,
        ):
            await handle_renewal(
                mock_uow, user, event, event_publisher=MagicMock(publish=AsyncMock())
            )

        assert subscription.status == "active"
        assert subscription.expires_at is not None

    async def test_handle_cancellation(self, mock_uow):
        """Test handling cancellation event."""
        user = MagicMock(id="user_123")
        subscription = MagicMock()
        event = {"app_user_id": "user_123"}

        # Mock existing subscription (async)
        with patch(
            "src.api.routes.v1.webhook_subscription_lifecycle.get_or_create_subscription",
            new_callable=AsyncMock,
            return_value=subscription,
        ):
            await handle_cancellation(
                mock_uow, user, event, event_publisher=MagicMock(publish=AsyncMock())
            )

        assert subscription.status == "cancelled"
        assert subscription.cancelled_at is not None

    async def test_handle_expiration(self, mock_uow):
        """Test handling expiration event."""
        user = MagicMock(id="user_123")
        subscription = MagicMock()
        event = {"app_user_id": "user_123"}

        # Mock existing subscription (async)
        with patch(
            "src.api.routes.v1.webhook_subscription_lifecycle.get_or_create_subscription",
            new_callable=AsyncMock,
            return_value=subscription,
        ):
            await handle_expiration(
                mock_uow, user, event, event_publisher=MagicMock(publish=AsyncMock())
            )

        assert subscription.status == "expired"

    async def test_handle_billing_issue(self, mock_uow):
        """Test handling billing issue event."""
        user = MagicMock(id="user_123")
        subscription = MagicMock()
        event = {"app_user_id": "user_123"}
        publisher = MagicMock(publish=AsyncMock())

        # Mock existing subscription (async)
        with patch(
            "src.api.routes.v1.webhook_subscription_lifecycle.get_or_create_subscription",
            new_callable=AsyncMock,
            return_value=subscription,
        ):
            await handle_billing_issue(mock_uow, user, event, event_publisher=publisher)

        assert subscription.status == "billing_issue"
        payload = publisher.publish.await_args.args[0]
        assert payload["data"]["lifecycle_type"] == "subscription_billing_issue"

    async def test_handle_transfer_updates_known_subscription(self, mock_uow):
        """Test transfer remaps existing subscription to the canonical subscriber ID."""
        subscription = MagicMock()
        event = {
            "transferred_from": ["$RCAnonymousID:old"],
            "transferred_to": ["firebase_uid_123", "$RCAnonymousID:new"],
        }
        mock_uow.subscriptions = MagicMock()
        mock_uow.subscriptions.find_by_revenuecat_id = AsyncMock(
            return_value=subscription
        )

        await handle_transfer(mock_uow, event)

        assert subscription.revenuecat_subscriber_id == "firebase_uid_123"
        assert subscription.updated_at is not None

    async def test_handle_product_change(self, mock_uow):
        """Test handling product change event (e.g., monthly to yearly)."""
        user = MagicMock(id="user_123")
        subscription = MagicMock()
        event = {"app_user_id": "user_123", "product_id": "premium_yearly"}
        publisher = MagicMock(publish=AsyncMock())

        with patch(
            "src.api.routes.v1.webhook_subscription_lifecycle.get_or_create_subscription",
            new_callable=AsyncMock,
            return_value=subscription,
        ):
            await handle_product_change(
                mock_uow, user, event, event_publisher=publisher
            )

        assert subscription.product_id == "premium_yearly"
        assert subscription.status == "active"
        assert subscription.updated_at is not None
        payload = publisher.publish.await_args.args[0]
        assert payload["data"]["lifecycle_type"] == "subscription_product_changed"

    async def test_handle_refund(self, mock_uow):
        """Test handling refund event: marks subscription refunded and revokes referral."""
        user = MagicMock(id="user_123")
        subscription = MagicMock()
        event = {"app_user_id": "user_123", "transaction_id": "tx_1"}

        with (
            patch(
                "src.api.routes.v1.webhook_subscription_lifecycle.get_or_create_subscription",
                new_callable=AsyncMock,
                return_value=subscription,
            ),
            patch(
                "src.api.routes.v1.webhook_subscription_lifecycle.revoke_referral_on_refund",
                new_callable=AsyncMock,
            ) as revoke_mock,
        ):
            await handle_refund(
                mock_uow, user, event, event_publisher=MagicMock(publish=AsyncMock())
            )

        assert subscription.status == "refunded"
        assert subscription.updated_at is not None
        revoke_mock.assert_awaited_once_with(mock_uow, "user_123")

    async def test_credit_referral_on_purchase_credits_wallet_for_real(self):
        """REAL money movement: pending conversion → converted + wallet credited
        by the exact commission_amount_vnd. Uses a fake repo that performs the
        same arithmetic as ReferralRepository, not a mocked-away function.
        """
        conversion = ReferralConversion(
            referrer_user_id="referrer_1",
            referred_user_id="referred_1",
            code_used="ABC123",
            status="pending",
            commission_amount=2.0,
            commission_currency="USD",
            commission_amount_vnd=50000,
        )
        repo = _FakeReferralRepo(conversion=conversion)
        uow = MagicMock()
        uow.referrals = repo

        await _credit_referral_on_purchase(uow, "referred_1")

        assert conversion.status == "converted"
        assert conversion.converted_at is not None
        assert repo.wallet.balance == 50000
        assert repo.wallet.total_earned == 50000

    async def test_credit_referral_on_purchase_noop_when_not_pending(self):
        """A conversion that is already converted/revoked must not be re-credited."""
        conversion = ReferralConversion(
            referrer_user_id="referrer_1",
            referred_user_id="referred_1",
            code_used="ABC123",
            status="converted",
            commission_amount=2.0,
            commission_currency="USD",
            commission_amount_vnd=50000,
        )
        repo = _FakeReferralRepo(conversion=conversion)
        uow = MagicMock()
        uow.referrals = repo

        await _credit_referral_on_purchase(uow, "referred_1")

        assert conversion.status == "converted"  # unchanged
        assert repo.wallet.balance == 0  # no double-credit

    async def test_credit_referral_on_purchase_noop_when_no_conversion(self):
        """No matching conversion row → no-op, no wallet touched."""
        repo = _FakeReferralRepo(conversion=None)
        uow = MagicMock()
        uow.referrals = repo

        await _credit_referral_on_purchase(uow, "referred_1")

        assert repo.wallet.balance == 0

    async def test_revoke_referral_on_refund_revokes_wallet_for_real(self):
        """REAL money movement: converted conversion → revoked + wallet balance
        reduced by the exact commission_amount_vnd (floored at 0).
        """
        conversion = ReferralConversion(
            referrer_user_id="referrer_1",
            referred_user_id="referred_1",
            code_used="ABC123",
            status="converted",
            commission_amount=2.0,
            commission_currency="USD",
            commission_amount_vnd=50000,
        )
        wallet = ReferralWallet(
            user_id="referrer_1",
            balance=80000,
            total_earned=80000,
            total_revoked=0,
            total_withdrawn=0,
            updated_at=utc_now(),
        )
        repo = _FakeReferralRepo(conversion=conversion, wallet=wallet)
        uow = MagicMock()
        uow.referrals = repo

        await _revoke_referral_on_refund(uow, "referred_1")

        assert conversion.status == "revoked"
        assert conversion.revoked_at is not None
        assert repo.wallet.balance == 30000
        assert repo.wallet.total_revoked == 50000

    async def test_revoke_referral_on_refund_floors_balance_at_zero(self):
        """Revoking more than the current balance floors at 0, never negative."""
        conversion = ReferralConversion(
            referrer_user_id="referrer_1",
            referred_user_id="referred_1",
            code_used="ABC123",
            status="converted",
            commission_amount=2.0,
            commission_currency="USD",
            commission_amount_vnd=50000,
        )
        wallet = ReferralWallet(
            user_id="referrer_1",
            balance=10000,  # already partially withdrawn/spent
            total_earned=50000,
            total_revoked=0,
            total_withdrawn=40000,
            updated_at=utc_now(),
        )
        repo = _FakeReferralRepo(conversion=conversion, wallet=wallet)
        uow = MagicMock()
        uow.referrals = repo

        await _revoke_referral_on_refund(uow, "referred_1")

        assert repo.wallet.balance == 0
        assert repo.wallet.total_revoked == 50000

    async def test_revoke_referral_on_refund_noop_when_not_converted(self):
        """A conversion still pending (never credited) must not be revoked."""
        conversion = ReferralConversion(
            referrer_user_id="referrer_1",
            referred_user_id="referred_1",
            code_used="ABC123",
            status="pending",
            commission_amount=2.0,
            commission_currency="USD",
            commission_amount_vnd=50000,
        )
        repo = _FakeReferralRepo(conversion=conversion)
        uow = MagicMock()
        uow.referrals = repo

        await _revoke_referral_on_refund(uow, "referred_1")

        assert conversion.status == "pending"  # unchanged
        assert repo.wallet.balance == 0

    async def test_webhook_product_change_end_to_end_returns_success(
        self, mock_request, mock_event_publisher
    ):
        """Full webhook path for PRODUCT_CHANGE: documents current 200/success semantics."""
        mock_request.json.return_value = {
            "event": {
                "type": "PRODUCT_CHANGE",
                "app_user_id": "user_123",
                "product_id": "premium_yearly",
                "store": "APP_STORE",
                "environment": "PRODUCTION",
            }
        }
        mock_user = MagicMock(id="user_123")
        subscription = MagicMock()

        with patch("src.api.routes.v1.webhooks.os.getenv", return_value="test_secret"):
            with (
                patch(
                    "src.api.routes.v1.webhooks._get_event_publisher",
                    return_value=mock_event_publisher,
                ),
                patch("src.api.routes.v1.webhooks.AsyncUnitOfWork") as mock_uow_class,
            ):
                mock_uow = MagicMock()
                mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
                mock_uow.__aexit__ = AsyncMock(return_value=False)
                mock_result = MagicMock()
                mock_result.scalars.return_value.first.return_value = mock_user
                mock_uow.session.execute = AsyncMock(return_value=mock_result)
                mock_uow_class.return_value = mock_uow

                with patch(
                    "src.api.routes.v1.webhook_subscription_lifecycle.get_or_create_subscription",
                    new_callable=AsyncMock,
                    return_value=subscription,
                ):
                    result = await revenuecat_webhook(
                        mock_request, authorization="test_secret"
                    )

        assert result == {"status": "success"}
        assert subscription.product_id == "premium_yearly"

    async def test_webhook_refund_end_to_end_returns_success(
        self, mock_request, mock_event_publisher
    ):
        """Full webhook path for REFUND: documents current 200/success semantics
        and confirms referral revocation runs inside the same webhook flow.
        """
        mock_request.json.return_value = {
            "event": {
                "type": "REFUND",
                "app_user_id": "user_123",
                "transaction_id": "tx_1",
                "environment": "PRODUCTION",
            }
        }
        mock_user = MagicMock(id="user_123")
        subscription = MagicMock()

        with patch("src.api.routes.v1.webhooks.os.getenv", return_value="test_secret"):
            with (
                patch(
                    "src.api.routes.v1.webhooks._get_event_publisher",
                    return_value=mock_event_publisher,
                ),
                patch("src.api.routes.v1.webhooks.AsyncUnitOfWork") as mock_uow_class,
            ):
                mock_uow = MagicMock()
                mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
                mock_uow.__aexit__ = AsyncMock(return_value=False)
                mock_result = MagicMock()
                mock_result.scalars.return_value.first.return_value = mock_user
                mock_uow.session.execute = AsyncMock(return_value=mock_result)
                mock_uow_class.return_value = mock_uow

                with (
                    patch(
                        "src.api.routes.v1.webhook_subscription_lifecycle.get_or_create_subscription",
                        new_callable=AsyncMock,
                        return_value=subscription,
                    ),
                    patch(
                        "src.api.routes.v1.webhook_subscription_lifecycle.revoke_referral_on_refund",
                        new_callable=AsyncMock,
                    ) as revoke_mock,
                ):
                    result = await revenuecat_webhook(
                        mock_request, authorization="test_secret"
                    )

        assert result == {"status": "success"}
        assert subscription.status == "refunded"
        revoke_mock.assert_awaited_once_with(mock_uow, "user_123")

    async def test_webhook_product_change_user_not_found_is_ignored(self, mock_request):
        """PRODUCT_CHANGE is in NON_RETRYABLE_USERLESS_EVENTS: ACK (not 404) when
        no local user matches, so RevenueCat does not retry-storm on it.
        """
        mock_request.json.return_value = {
            "event": {
                "type": "PRODUCT_CHANGE",
                "app_user_id": "$RCAnonymousID:missing",
                "product_id": "premium_yearly",
            }
        }

        with patch("src.api.routes.v1.webhooks.os.getenv", return_value="test_secret"):
            with patch("src.api.routes.v1.webhooks.AsyncUnitOfWork") as mock_uow_class:
                mock_uow = MagicMock()
                mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
                mock_uow.__aexit__ = AsyncMock(return_value=False)
                mock_uow.subscriptions = MagicMock()
                mock_uow.subscriptions.find_by_revenuecat_id = AsyncMock(
                    return_value=None
                )
                mock_result = MagicMock()
                mock_result.scalars.return_value.first.return_value = None
                mock_uow.session.execute = AsyncMock(return_value=mock_result)
                mock_uow_class.return_value = mock_uow

                result = await revenuecat_webhook(
                    mock_request, authorization="test_secret"
                )

        assert result == {"status": "ignored", "reason": "user_not_found"}

    async def test_webhook_refund_user_not_found_is_ignored(self, mock_request):
        """REFUND is in NON_RETRYABLE_USERLESS_EVENTS: ACK (not 404) when no
        local user matches.
        """
        mock_request.json.return_value = {
            "event": {
                "type": "REFUND",
                "app_user_id": "$RCAnonymousID:missing",
                "transaction_id": "tx_1",
            }
        }

        with patch("src.api.routes.v1.webhooks.os.getenv", return_value="test_secret"):
            with patch("src.api.routes.v1.webhooks.AsyncUnitOfWork") as mock_uow_class:
                mock_uow = MagicMock()
                mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
                mock_uow.__aexit__ = AsyncMock(return_value=False)
                mock_uow.subscriptions = MagicMock()
                mock_uow.subscriptions.find_by_revenuecat_id = AsyncMock(
                    return_value=None
                )
                mock_result = MagicMock()
                mock_result.scalars.return_value.first.return_value = None
                mock_uow.session.execute = AsyncMock(return_value=mock_result)
                mock_uow_class.return_value = mock_uow

                result = await revenuecat_webhook(
                    mock_request, authorization="test_secret"
                )

        assert result == {"status": "ignored", "reason": "user_not_found"}
