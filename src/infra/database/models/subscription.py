"""
Subscription model for tracking user subscriptions.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    event,
    text,
)
from sqlalchemy.orm import relationship

from src.domain.model.subscription_access import (
    SubscriptionAccessUnavailable,
    evaluate_subscription_access,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.base import Base
from src.infra.database.models.base import BaseMixin


class Subscription(Base, BaseMixin):
    """
    Access rows from every payment provider (``provider``).

    RevenueCat rows (provider='revenuecat') are written from RevenueCat
    snapshots and webhooks and carry the RevenueCat app_user_id in
    ``revenuecat_subscriber_id``. Rows from other providers (MoMo, ...)
    leave that column NULL and are never read or written by RevenueCat code.
    """

    __tablename__ = "subscriptions"

    # User relationship
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # RevenueCat app_user_id; NULL on rows another provider owns.
    revenuecat_subscriber_id = Column(String(255), nullable=True, index=True)
    # Every ORM writer names its provider (enforced on insert below). The
    # server default only serves writers that predate the column -- an
    # image rolled back past it, or manual SQL -- which are all RevenueCat.
    provider = Column(
        String(32),
        nullable=False,
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
    # RevenueCat time of an event applied without a verified snapshot. While
    # set, the access policy treats the row as unresolved; the next snapshot
    # taken at or after this time clears it.
    provider_sync_required_at = Column(DateTime(timezone=True), nullable=True)
    # RevenueCat time of a TRANSFER that moved this user's purchases to
    # another app user id while its customer could not be read. Until a
    # snapshot taken at or after it is applied, an empty customer is
    # authoritative for the user ("RevenueCat moved it away"), so refresh and
    # repair can revoke what the transfer took instead of leaving it
    # unresolved for good.
    provider_transfer_source_at = Column(DateTime(timezone=True), nullable=True)
    # RevenueCat time of a refund whose customer could not be read or
    # applied. The refund may have ended the paid period the row still shows,
    # so until a snapshot taken at or after it is applied, the paid-route
    # fallback never grants the row; that snapshot then decides.
    provider_refund_pending_at = Column(DateTime(timezone=True), nullable=True)
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
        # Only rows carrying a provider marker (normally none): the support
        # repair queue and the monitoring endpoint, polled every 5 minutes,
        # read these instead of scanning every subscription.
        Index(
            "ix_subscriptions_access_markers",
            "user_id",
            postgresql_where=text(
                "provider_sync_required_at IS NOT NULL"
                " OR provider_refund_pending_at IS NOT NULL"
                " OR provider_transfer_source_at IS NOT NULL"
            ),
        ),
    )

    def is_active(self, now: datetime | None = None) -> bool:
        """Legacy active-row check retained until the database policy is enabled.

        Rows never verified, or changed by an event whose snapshot could not
        be read, keep the pre-policy behaviour while the flag is off.
        ``now`` defaults to the current time.
        """
        instant = now or utc_now()
        if (
            self.provider_snapshot_at is None
            or self.provider_sync_required_at is not None
        ):
            return bool(
                self.status == "active"
                and (self.expires_at is None or instant < self.expires_at)
            )
        try:
            return evaluate_subscription_access([self], now=instant).has_access
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


@event.listens_for(Subscription, "before_insert")
def _require_explicit_provider(mapper, connection, target: Subscription) -> None:
    """A row inserted without a provider would read as RevenueCat's, and the
    next RevenueCat snapshot would expire it. New writers must say whose it is.
    """
    if not target.provider:
        raise ValueError("Subscription.provider must be set explicitly on insert")
