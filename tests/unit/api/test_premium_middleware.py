"""
Unit tests for subscription access middleware.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from src.api.middleware.premium_check import get_subscription_status


@pytest.mark.asyncio
class TestSubscriptionMiddleware:
    """Test suite for subscription middleware."""

    @pytest.fixture
    def mock_request(self):
        """Create mock request with user."""
        request = MagicMock(spec=Request)
        request.state = MagicMock()
        return request

    @pytest.fixture
    def mock_user_with_subscription(self):
        """Create mock user with active subscription."""
        user = MagicMock()
        user.id = "user_123"
        user.has_active_subscription.return_value = True

        subscription = MagicMock()
        subscription.product_id = "standard_monthly"
        subscription.expires_at = datetime.now() + timedelta(days=30)
        subscription.is_monthly.return_value = True
        subscription.is_yearly.return_value = False

        user.get_active_subscription.return_value = subscription
        return user

    @pytest.fixture
    def mock_user_without_subscription(self):
        """Create mock user without subscription."""
        user = MagicMock()
        user.id = "user_456"
        user.firebase_uid = "firebase_uid_456"
        user.has_active_subscription.return_value = False
        user.get_active_subscription.return_value = None
        return user

    async def test_get_subscription_status_with_active_subscription(
        self, mock_request, mock_user_with_subscription
    ):
        """Test get_subscription_status returns correct info with subscription."""
        mock_request.state.user = mock_user_with_subscription

        result = await get_subscription_status(mock_request)

        assert result["has_subscription"] is True
        assert result["subscription"]["product_id"] == "standard_monthly"
        assert result["subscription"]["is_monthly"] is True
        assert result["source"] == "cache"

    async def test_get_subscription_status_without_user(self, mock_request):
        """Test get_subscription_status without authenticated user."""
        mock_request.state.user = None

        result = await get_subscription_status(mock_request)

        assert result["has_subscription"] is False
        assert result["subscription"] is None
        assert result["source"] == "no_user"

    async def test_get_subscription_status_checks_revenuecat(
        self, mock_request, mock_user_without_subscription
    ):
        """Test get_subscription_status checks RevenueCat when no local subscription."""
        mock_request.state.user = mock_user_without_subscription

        # Create mock subscription service
        mock_service = AsyncMock()
        mock_service.get_subscription_info = AsyncMock(
            return_value={
                "product_id": "standard_yearly",
                "expires_date": datetime.now() + timedelta(days=365),
                "store": "APP_STORE",
                "is_active": True,
            }
        )

        with patch(
            "src.api.middleware.premium_check._get_subscription_service",
            return_value=mock_service,
        ):
            with patch("src.api.middleware.premium_check.os.getenv") as mock_getenv:
                mock_getenv.return_value = "test_key"

                result = await get_subscription_status(mock_request)

                assert result["has_subscription"] is True
                assert result["subscription"]["product_id"] == "standard_yearly"
                assert result["source"] == "revenuecat_api"

    async def test_get_subscription_status_no_subscription(
        self, mock_request, mock_user_without_subscription
    ):
        """Test get_subscription_status when user has no subscription."""
        mock_request.state.user = mock_user_without_subscription

        # Create mock subscription service
        mock_service = AsyncMock()
        mock_service.get_subscription_info = AsyncMock(return_value=None)

        with patch(
            "src.api.middleware.premium_check._get_subscription_service",
            return_value=mock_service,
        ):
            with patch("src.api.middleware.premium_check.os.getenv") as mock_getenv:
                mock_getenv.return_value = "test_key"

                result = await get_subscription_status(mock_request)

                assert result["has_subscription"] is False
                assert result["subscription"] is None
                assert result["source"] == "none"
