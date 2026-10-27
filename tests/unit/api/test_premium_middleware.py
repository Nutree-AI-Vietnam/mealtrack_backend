"""
Unit tests for subscription access middleware.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException, Request

from src.api.exceptions import ExternalServiceException
from src.api.middleware import premium_check
from src.api.middleware.premium_check import (
    get_subscription_status,
    require_subscription,
)
from src.domain.ports.subscription_service_port import EntitlementCheck
from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter

USER_ID = "user_456"


class _Subscriptions:
    def __init__(self, entitled):
        self.entitled = entitled
        self.calls: list[tuple[str, bool]] = []

    async def has_entitled_subscription(self, user_id, *, exclude_sandbox):
        self.calls.append((user_id, exclude_sandbox))
        return self.entitled


class _Users:
    def __init__(self, firebase_uid):
        self.firebase_uid = firebase_uid

    async def find_by_id(self, user_id):
        if self.firebase_uid is None:
            return None
        return SimpleNamespace(id=user_id, firebase_uid=self.firebase_uid)


class _Uow:
    def __init__(self, entitled=False, firebase_uid="firebase_uid_456"):
        self.subscriptions = _Subscriptions(entitled)
        self.users = _Users(firebase_uid)
        self.opened = 0

    async def __aenter__(self):
        self.opened += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Cache:
    def __init__(self):
        self.values: dict[str, object] = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ttl_seconds):
        self.values[key] = value


@pytest.fixture
def access(monkeypatch):
    """Wire the check to fakes; returns them for assertions."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENABLE_DEV_AUTH_BYPASS", raising=False)
    monkeypatch.setenv("REVENUECAT_SECRET_API_KEY", "test_key")
    state = SimpleNamespace(
        uow=_Uow(),
        cache=_Cache(),
        revenuecat=AsyncMock(),
    )
    state.revenuecat.check_entitlement = AsyncMock(
        return_value=EntitlementCheck.NOT_ENTITLED
    )
    monkeypatch.setattr(premium_check, "_get_uow_factory", lambda: lambda: state.uow)
    monkeypatch.setattr(premium_check, "_get_cache", lambda: state.cache)
    monkeypatch.setattr(
        premium_check, "_get_subscription_service", lambda: state.revenuecat
    )
    return state


@pytest.mark.asyncio
class TestRequireSubscription:
    """The Premium check behind the Ghi nhanh routes."""

    async def test_entitled_row_allows_without_revenuecat(self, access):
        access.uow = _Uow(entitled=True)

        assert await require_subscription(USER_ID) is None

        access.revenuecat.check_entitlement.assert_not_called()

    async def test_dev_bypass_allows_without_any_lookup(self, access, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("ENABLE_DEV_AUTH_BYPASS", "1")

        assert await require_subscription(USER_ID) is None

        assert access.uow.opened == 0

    async def test_unset_environment_never_allows(self, access, monkeypatch):
        monkeypatch.setenv("ENABLE_DEV_AUTH_BYPASS", "1")

        with pytest.raises(HTTPException) as exc_info:
            await require_subscription(USER_ID)

        assert exc_info.value.status_code == 402
        assert exc_info.value.detail["error_code"] == "SUBSCRIPTION_REQUIRED"

    async def test_revenuecat_fallback_allows_fresh_purchase(self, access):
        access.revenuecat.check_entitlement.return_value = EntitlementCheck.ENTITLED

        assert await require_subscription(USER_ID) is None

        access.revenuecat.check_entitlement.assert_awaited_once_with(
            "firebase_uid_456", timeout_seconds=3.0
        )

    async def test_no_subscription_anywhere_gives_402(self, access):
        with pytest.raises(HTTPException) as exc_info:
            await require_subscription(USER_ID)

        assert exc_info.value.status_code == 402
        assert exc_info.value.detail["error_code"] == "SUBSCRIPTION_REQUIRED"

    async def test_without_revenuecat_config_gives_402(self, access, monkeypatch):
        monkeypatch.setenv("REVENUECAT_SECRET_API_KEY", "")

        with pytest.raises(HTTPException) as exc_info:
            await require_subscription(USER_ID)

        assert exc_info.value.status_code == 402
        access.revenuecat.check_entitlement.assert_not_called()

    async def test_unknown_user_gives_402_without_revenuecat(self, access):
        access.uow = _Uow(firebase_uid=None)

        with pytest.raises(HTTPException) as exc_info:
            await require_subscription(USER_ID)

        assert exc_info.value.status_code == 402
        access.revenuecat.check_entitlement.assert_not_called()

    async def test_revenuecat_5xx_gives_503_and_is_not_cached(
        self, access, monkeypatch
    ):
        response = httpx.Response(
            503, request=httpx.Request("GET", "https://api.revenuecat.com")
        )
        adapter = RevenueCatAdapter(api_key="test_key")
        monkeypatch.setattr(premium_check, "_get_subscription_service", lambda: adapter)
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=response
            )
            with pytest.raises(ExternalServiceException) as exc_info:
                await require_subscription(USER_ID)

        assert exc_info.value.error_code == "SUBSCRIPTION_CHECK_UNAVAILABLE"
        assert access.cache.values == {}

    async def test_production_ignores_sandbox_rows(self, access, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")

        with pytest.raises(HTTPException):
            await require_subscription(USER_ID)

        assert access.uow.subscriptions.calls == [(USER_ID, True)]

    async def test_other_environments_accept_sandbox_rows(self, access, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "staging")
        access.uow = _Uow(entitled=True)

        await require_subscription(USER_ID)

        assert access.uow.subscriptions.calls == [(USER_ID, False)]

    async def test_access_is_cached_per_user(self, access):
        access.uow = _Uow(entitled=True)
        await require_subscription(USER_ID)
        access.uow = _Uow()

        await require_subscription(USER_ID)

        assert access.uow.opened == 0
        assert access.cache.values == {
            "subscription_access:user_456": EntitlementCheck.ENTITLED.value
        }

    async def test_purchase_after_402_counts_on_next_request(self, access):
        with pytest.raises(HTTPException):
            await require_subscription(USER_ID)
        assert access.cache.values == {}

        access.revenuecat.check_entitlement.return_value = EntitlementCheck.ENTITLED

        assert await require_subscription(USER_ID) is None

    async def test_402_cached_before_the_fix_is_ignored(self, access):
        access.cache.values["subscription_access:user_456"] = (
            EntitlementCheck.NOT_ENTITLED.value
        )
        access.revenuecat.check_entitlement.return_value = EntitlementCheck.ENTITLED

        assert await require_subscription(USER_ID) is None

    async def test_cache_outage_still_checks(self, access):
        access.cache.get = AsyncMock(side_effect=ConnectionError("redis down"))
        access.uow = _Uow(entitled=True)

        assert await require_subscription(USER_ID) is None


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
