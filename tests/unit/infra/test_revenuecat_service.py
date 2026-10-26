"""
Unit tests for RevenueCat adapter.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest

from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter


@pytest.mark.asyncio
class TestRevenueCatAdapter:
    """Test suite for RevenueCat adapter."""

    @pytest.fixture
    def service(self):
        """Create RevenueCat adapter instance."""
        return RevenueCatAdapter(api_key="test_api_key")

    @pytest.fixture
    def mock_subscriber_response(self):
        """Mock subscriber response from RevenueCat API."""
        return {
            "subscriber": {
                "entitlements": {
                    "standard": {
                        "expires_date": (
                            datetime.now() + timedelta(days=30)
                        ).isoformat()
                        + "Z",
                        "product_identifier": "standard_monthly",
                        "purchase_date": datetime.now().isoformat() + "Z",
                    }
                },
                "subscriptions": {
                    "standard_monthly": {
                        "expires_date": (
                            datetime.now() + timedelta(days=30)
                        ).isoformat()
                        + "Z",
                        "store": "APP_STORE",
                    }
                },
            }
        }

    async def test_get_subscriber_info_success(self, service, mock_subscriber_response):
        """Test successful retrieval of subscriber info."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_subscriber_response
            mock_response.raise_for_status = MagicMock()

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await service.get_subscriber_info("user_123")

            assert result == mock_subscriber_response
            mock_client.return_value.__aenter__.return_value.get.assert_called_once()

    async def test_get_subscriber_info_not_found(self, service):
        """Test subscriber not found returns None."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 404

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await service.get_subscriber_info("user_123")

            assert result is None

    async def test_get_subscriber_info_http_error(self, service):
        """Test HTTP error returns None."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=httpx.HTTPError("Connection error")
            )

            result = await service.get_subscriber_info("user_123")

            assert result is None

    async def test_has_active_subscription_with_active_subscription(
        self, service, mock_subscriber_response
    ):
        """Test subscription is active when subscription is active."""
        with patch.object(
            service, "get_subscriber_info", return_value=mock_subscriber_response
        ):
            result = await service.has_active_subscription("user_123")
            assert result is True

    async def test_has_active_subscription_with_expired_subscription(self, service):
        """Test subscription is not active when subscription is expired."""
        expired_response = {
            "subscriber": {
                "entitlements": {
                    "standard": {
                        "expires_date": (datetime.now() - timedelta(days=1)).isoformat()
                        + "Z",
                        "product_identifier": "standard_monthly",
                        "purchase_date": (
                            datetime.now() - timedelta(days=31)
                        ).isoformat()
                        + "Z",
                    }
                },
                "subscriptions": {},
            }
        }

        with patch.object(
            service, "get_subscriber_info", return_value=expired_response
        ):
            result = await service.has_active_subscription("user_123")
            assert result is False

    async def test_has_active_subscription_with_lifetime_subscription(self, service):
        """Test subscription is active with lifetime subscription (no expiry)."""
        lifetime_response = {
            "subscriber": {
                "entitlements": {
                    "standard": {
                        "expires_date": None,  # Lifetime access
                        "product_identifier": "standard_lifetime",
                        "purchase_date": datetime.now().isoformat() + "Z",
                    }
                },
                "subscriptions": {},
            }
        }

        with patch.object(
            service, "get_subscriber_info", return_value=lifetime_response
        ):
            result = await service.has_active_subscription("user_123")
            assert result is True

    async def test_has_active_subscription_no_entitlement(self, service):
        """Test subscription is not active when no standard entitlement exists."""
        no_entitlement_response = {
            "subscriber": {"entitlements": {}, "subscriptions": {}}
        }

        with patch.object(
            service, "get_subscriber_info", return_value=no_entitlement_response
        ):
            result = await service.has_active_subscription("user_123")
            assert result is False

    async def test_has_active_subscription_no_subscriber(self, service):
        """Test subscription is not active when subscriber not found."""
        with patch.object(service, "get_subscriber_info", return_value=None):
            result = await service.has_active_subscription("user_123")
            assert result is False

    async def test_get_subscription_info_with_active_subscription(
        self, service, mock_subscriber_response
    ):
        """Test getting active subscription info."""
        with patch.object(
            service, "get_subscriber_info", return_value=mock_subscriber_response
        ):
            result = await service.get_subscription_info("user_123")

            assert result is not None
            assert result["product_id"] == "standard_monthly"
            assert result["store"] == "APP_STORE"
            assert result["is_active"] is True

    async def test_get_subscription_info_no_active_subscription(self, service):
        """Test getting subscription info when no active subscription."""
        expired_response = {
            "subscriber": {
                "entitlements": {},
                "subscriptions": {
                    "standard_monthly": {
                        "expires_date": (datetime.now() - timedelta(days=1)).isoformat()
                        + "Z",
                        "store": "APP_STORE",
                    }
                },
            }
        }

        with patch.object(
            service, "get_subscriber_info", return_value=expired_response
        ):
            result = await service.get_subscription_info("user_123")
            assert result is None
