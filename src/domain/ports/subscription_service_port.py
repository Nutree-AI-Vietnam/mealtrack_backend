"""
Port for subscription services following clean architecture.
Enables subscription checking without depending on specific implementations.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


class RevenueCatSnapshotUnavailable(RuntimeError):
    """Provider state could not be safely verified."""

    def __init__(self, message: str, retry_after_seconds: float | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class RevenueCatSnapshot:
    subscriber: dict | None
    request_date: datetime | None
    absent: bool = False


class SubscriptionServicePort(ABC):
    """Port for subscription operations."""

    @abstractmethod
    async def get_verified_snapshot(self, app_user_id: str) -> RevenueCatSnapshot:
        """Return a verified snapshot or raise on provider/malformed-state errors."""
        pass

    @abstractmethod
    async def get_subscriber_info(self, app_user_id: str) -> dict | None:
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
    async def get_subscription_info(self, app_user_id: str) -> dict | None:
        """
        Get active subscription details.

        Args:
            app_user_id: Your user ID

        Returns:
            Subscription details (product_id, expires_date, store, is_active) if active
        """
        pass
