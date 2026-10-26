"""Fake subscription repository for testing."""

from datetime import datetime, timedelta

from src.domain.model.subscription import Subscription
from src.domain.ports.subscription_repository_port import SubscriptionRepositoryPort


class FakeSubscriptionRepository(SubscriptionRepositoryPort):
    """In-memory implementation of SubscriptionRepositoryPort for testing."""

    def __init__(self):
        self._subscriptions: dict[str, Subscription] = {}

    async def save(self, subscription: Subscription) -> Subscription:
        """Save a subscription."""
        self._subscriptions[subscription.id] = subscription
        return subscription

    async def find_by_id(self, subscription_id: str) -> Subscription | None:
        """Find a subscription by ID."""
        return self._subscriptions.get(subscription_id)

    async def find_by_user_id(self, user_id: str) -> list[Subscription]:
        """Find all subscriptions for a user."""
        return [sub for sub in self._subscriptions.values() if sub.user_id == user_id]

    async def find_active_by_user_id(self, user_id: str) -> Subscription | None:
        """Find active subscription for a user."""
        for sub in self._subscriptions.values():
            if (
                sub.user_id == user_id
                and sub.status == "active"
                and (sub.expires_at is None or sub.expires_at > datetime.now())
            ):
                return sub
        return None

    async def find_expiring_soon(
        self, days_until_expiry: int = 7
    ) -> list[Subscription]:
        """Find subscriptions expiring within specified days."""
        cutoff = datetime.now() + timedelta(days=days_until_expiry)
        return [
            sub
            for sub in self._subscriptions.values()
            if sub.expires_at and datetime.now() < sub.expires_at <= cutoff
        ]

    async def find_expiring_in_window(
        self,
        from_days: int,
        to_days: int,
        now: datetime | None = None,
    ) -> list[Subscription]:
        """Active subs whose expires_at falls within the [from_days, to_days) window."""
        reference = now or datetime.now()
        lower = reference + timedelta(days=from_days)
        upper = reference + timedelta(days=to_days)
        return [
            sub
            for sub in self._subscriptions.values()
            if sub.status == "active"
            and sub.expires_at is not None
            and lower <= sub.expires_at < upper
        ]

    async def cancel(self, subscription_id: str, reason: str = None) -> bool:
        """Cancel a subscription."""
        if subscription_id in self._subscriptions:
            sub = self._subscriptions[subscription_id]
            sub.status = "cancelled"
            return True
        return False

    async def reactivate(self, subscription_id: str) -> bool:
        """Reactivate a cancelled subscription."""
        if subscription_id in self._subscriptions:
            sub = self._subscriptions[subscription_id]
            sub.status = "active"
            return True
        return False

    async def update_payment_status(
        self, subscription_id: str, payment_status: str, payment_date: datetime = None
    ) -> bool:
        """Update payment status for a subscription."""
        if subscription_id in self._subscriptions:
            sub = self._subscriptions[subscription_id]
            sub.payment_status = payment_status
            if payment_date:
                sub.last_payment_date = payment_date
            return True
        return False

    async def extend_trial(self, subscription_id: str, days: int) -> bool:
        """Extend trial period for a subscription."""
        if subscription_id in self._subscriptions:
            sub = self._subscriptions[subscription_id]
            if sub.trial_end_date:
                sub.trial_end_date = sub.trial_end_date + timedelta(days=days)
            return True
        return False
