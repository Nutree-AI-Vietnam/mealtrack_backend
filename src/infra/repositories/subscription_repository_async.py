"""Async subscription repository."""

from datetime import datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.ports.subscription_repository_port import SubscriptionRepositoryPort
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.subscription import Subscription


class AsyncSubscriptionRepository(SubscriptionRepositoryPort):
    """Async repository for subscription data. Never calls session.commit()."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, subscription: Subscription) -> Subscription:
        """Save or update a subscription."""
        if subscription.id:
            return await self.update(subscription)
        return await self.add(subscription)

    async def add(self, subscription: Subscription) -> Subscription:
        """Insert a new subscription."""
        self.session.add(subscription)
        await self.session.flush()
        return subscription

    async def update(self, subscription: Subscription) -> Subscription:
        """Merge an existing subscription."""
        merged = await self.session.merge(subscription)
        await self.session.flush()
        return merged

    async def find_by_id(self, subscription_id: str) -> Subscription | None:
        """Find a subscription by its primary key."""
        result = await self.session.execute(
            select(Subscription).where(Subscription.id == subscription_id)
        )
        return result.scalars().first()

    async def find_by_user_id(self, user_id: str) -> list[Subscription]:
        """Find all subscriptions for a user."""
        result = await self.session.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        )
        return list(result.scalars().all())

    async def find_active_by_user_id(self, user_id: str) -> Subscription | None:
        """Find the active subscription for a user."""
        result = await self.session.execute(
            select(Subscription).where(
                and_(
                    Subscription.user_id == user_id,
                    Subscription.status == "active",
                )
            )
        )
        return result.scalars().first()

    async def find_by_revenuecat_id(
        self, revenuecat_subscriber_id: str
    ) -> Subscription | None:
        """Find a subscription by RevenueCat subscriber ID."""
        result = await self.session.execute(
            select(Subscription).where(
                Subscription.revenuecat_subscriber_id == revenuecat_subscriber_id
            )
        )
        return result.scalars().first()

    async def find_expiring_soon(
        self, days_until_expiry: int = 7
    ) -> list[Subscription]:
        """Find active subscriptions expiring within specified days."""
        now = utc_now()
        expiry_threshold = now + timedelta(days=days_until_expiry)
        result = await self.session.execute(
            select(Subscription).where(
                and_(
                    Subscription.status == "active",
                    Subscription.expires_at <= expiry_threshold,
                    Subscription.expires_at > now,
                )
            )
        )
        return list(result.scalars().all())

    async def find_expiring_in_window(
        self,
        from_days: int,
        to_days: int,
        now: datetime | None = None,
    ) -> list[Subscription]:
        """Active subs whose expires_at falls within [reference+from_days, reference+to_days)."""
        reference = now or utc_now()
        lower = reference + timedelta(days=from_days)
        upper = reference + timedelta(days=to_days)
        result = await self.session.execute(
            select(Subscription).where(
                and_(
                    Subscription.status == "active",
                    Subscription.expires_at >= lower,
                    Subscription.expires_at < upper,
                )
            )
        )
        return list(result.scalars().all())

    async def cancel(self, subscription_id: str, reason: str = None) -> bool:
        """Cancel a subscription."""
        subscription = await self.find_by_id(subscription_id)
        if subscription:
            subscription.status = "cancelled"
            subscription.updated_at = utc_now()
            if reason:
                subscription.cancellation_reason = reason
            await self.session.flush()
            return True
        return False

    async def reactivate(self, subscription_id: str) -> bool:
        """Reactivate a cancelled subscription."""
        subscription = await self.find_by_id(subscription_id)
        if subscription and subscription.status == "cancelled":
            subscription.status = "active"
            subscription.updated_at = utc_now()
            await self.session.flush()
            return True
        return False

    async def extend_trial(self, subscription_id: str, days: int) -> bool:
        """Extend trial period for a subscription."""
        subscription = await self.find_by_id(subscription_id)
        if subscription and subscription.status == "trial":
            if subscription.expires_at:
                subscription.expires_at = subscription.expires_at + timedelta(days=days)
            else:
                subscription.expires_at = utc_now() + timedelta(days=days)
            subscription.updated_at = utc_now()
            await self.session.flush()
            return True
        return False

    async def update_payment_status(
        self,
        subscription_id: str,
        payment_status: str,
        payment_date: datetime = None,
    ) -> bool:
        """Update payment status for a subscription."""
        subscription = await self.find_by_id(subscription_id)
        if subscription:
            subscription.payment_status = payment_status
            if payment_date:
                subscription.payment_date = payment_date
            subscription.updated_at = utc_now()
            await self.session.flush()
            return True
        return False

    async def get_expired_subscriptions(self) -> list[Subscription]:
        """Get all active subscriptions that have passed their expiry date."""
        result = await self.session.execute(
            select(Subscription).where(
                and_(
                    Subscription.status == "active",
                    Subscription.expires_at < utc_now(),
                )
            )
        )
        return list(result.scalars().all())

    async def update_subscription_status(
        self,
        subscription_id: str,
        status: str,
        expires_at: datetime | None = None,
    ) -> Subscription | None:
        """Update subscription status."""
        subscription = await self.find_by_id(subscription_id)
        if subscription:
            subscription.status = status
            subscription.updated_at = utc_now()
            if expires_at:
                subscription.expires_at = expires_at
            await self.session.flush()
        return subscription
