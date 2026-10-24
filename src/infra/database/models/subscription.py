"""
Subscription model for tracking user subscriptions.
"""

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Index, String, text
from sqlalchemy.orm import relationship

from src.domain.services.subscription_access_policy import (
    SubscriptionAccessUnavailable,
    evaluate_subscription_access,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.base import Base
from src.infra.database.models.base import BaseMixin


class Subscription(Base, BaseMixin):
    """
    Stores subscription records synced from RevenueCat.

    RevenueCat is the source of truth - this table caches key data.
    """

    __tablename__ = "subscriptions"

    # User relationship
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # RevenueCat data
    revenuecat_subscriber_id = Column(String(255), nullable=False, index=True)
    provider = Column(
        String(32),
        nullable=False,
        default="revenuecat",
        server_default=text("'revenuecat'"),
    )
    product_id = Column(
        String(255), nullable=False
    )  # "premium_monthly" or "premium_yearly"
    platform = Column(Enum("ios", "android", "web", native_enum=False), nullable=False)

    # Subscription status
    status = Column(
        Enum(
            "active",
            "expired",
            "cancelled",
            "billing_issue",
            "refunded",
            native_enum=False,
        ),
        nullable=False,
        default="active",
    )
    purchased_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    grace_period_expires_at = Column(DateTime(timezone=True), nullable=True)
    period_type = Column(String(16), nullable=True)
    provider_snapshot_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    # Store metadata
    store_transaction_id = Column(String(255), nullable=True)
    is_sandbox = Column(Boolean, default=False, nullable=False)

    # Relationships
    user = relationship("User", back_populates="subscriptions")

    # Indexes
    __table_args__ = (
        Index("idx_user_id_status", "user_id", "status"),
        Index("idx_expires_at", "expires_at"),
        Index("idx_revenuecat_subscriber_id", "revenuecat_subscriber_id"),
    )

    def is_active(self) -> bool:
        """Legacy active-row check retained until the database policy is enabled."""
        if self.provider_snapshot_at is None:
            return self.status == "active" and (
                self.expires_at is None or utc_now() < self.expires_at
            )
        try:
            return evaluate_subscription_access([self]).has_access
        except SubscriptionAccessUnavailable:
            return False

    def is_monthly(self) -> bool:
        """Check if this is a monthly subscription."""
        return "monthly" in self.product_id.lower()

    def is_yearly(self) -> bool:
        """Check if this is a yearly subscription."""
        return (
            "yearly" in self.product_id.lower() or "annual" in self.product_id.lower()
        )
