"""
SubscriptionRepositoryPort - Interface for subscription repository operations.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.model.subscription import Subscription


class SubscriptionRepositoryPort(ABC):
    """Interface for subscription repository operations."""

    @abstractmethod
    async def save(self, subscription: Subscription) -> Subscription:
        """Save or update a subscription."""
        pass

    @abstractmethod
    async def find_by_id(self, subscription_id: str) -> Subscription | None:
        """Find a subscription by ID."""
        pass

    @abstractmethod
    async def find_by_user_id(self, user_id: str) -> list[Subscription]:
        """Find all subscriptions for a user."""
        pass

    @abstractmethod
    async def find_active_by_user_id(self, user_id: str) -> Subscription | None:
        """Find the active subscription for a user."""
        pass

    @abstractmethod
    async def find_expiring_soon(self, days_until_expiry: int = 7) -> list[Subscription]:
        """Find subscriptions expiring within specified days."""
        pass

    @abstractmethod
    async def find_expiring_in_window(
        self,
        from_days: int,
        to_days: int,
        now: datetime | None = None,
    ) -> list[Subscription]:
        """Active subs whose expires_at falls within [reference+from_days, reference+to_days).

        `now` lets callers pin the reference moment for deterministic tests and
        consistent window boundaries within a single scheduler run.
        """
        pass

    @abstractmethod
    async def cancel(self, subscription_id: str, reason: str = None) -> bool:
        """Cancel a subscription."""
        pass

    @abstractmethod
    async def reactivate(self, subscription_id: str) -> bool:
        """Reactivate a cancelled subscription."""
        pass

    @abstractmethod
    async def update_payment_status(
        self, subscription_id: str, payment_status: str, payment_date: datetime = None
    ) -> bool:
        """Update payment status for a subscription."""
        pass

    @abstractmethod
    async def extend_trial(self, subscription_id: str, days: int) -> bool:
        """Extend trial period for a subscription."""
        pass
