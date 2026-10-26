"""
Port for subscription services following clean architecture.
Enables subscription checking without depending on specific implementations.
"""

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Dict, Optional


class EntitlementCheck(StrEnum):
    """Answer of the subscription provider for one user.

    ``UNAVAILABLE`` means the provider could not answer; callers must not
    treat it as "not subscribed".
    """

    ENTITLED = "entitled"
    NOT_ENTITLED = "not_entitled"
    UNAVAILABLE = "unavailable"


class SubscriptionServicePort(ABC):
    """Port for subscription operations."""

    @abstractmethod
    async def get_subscriber_info(self, app_user_id: str) -> Optional[Dict]:
        """
        Get subscriber info from subscription provider.

        Args:
            app_user_id: Your user ID

        Returns:
            Subscriber data including entitlements and subscriptions
        """
        pass

    @abstractmethod
    async def has_active_subscription(self, app_user_id: str) -> bool:
        """
        Check if user has active subscription.

        Args:
            app_user_id: Your user ID

        Returns:
            True if user has active subscription, False otherwise
        """
        pass

    @abstractmethod
    async def check_entitlement(
        self, app_user_id: str, *, timeout_seconds: float = 3.0
    ) -> EntitlementCheck:
        """
        Check the standard entitlement, keeping provider failures apart.

        Args:
            app_user_id: Your user ID
            timeout_seconds: Upper bound for the provider call

        Returns:
            ENTITLED or NOT_ENTITLED, or UNAVAILABLE when the provider
            errored or timed out
        """
        pass

    @abstractmethod
    async def get_subscription_info(self, app_user_id: str) -> Optional[Dict]:
        """
        Get active subscription details.

        Args:
            app_user_id: Your user ID

        Returns:
            Subscription details (product_id, expires_date, store, is_active) if active
        """
        pass
