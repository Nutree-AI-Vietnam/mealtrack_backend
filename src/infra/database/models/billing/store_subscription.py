"""Store subscriptions as reported by each customer's current provider snapshot."""

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.base import Base


class StoreSubscriptionORM(Base):
    """Written only by the entitlement sync service (snapshot replace)."""

    __tablename__ = "store_subscriptions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    provider = Column(String(32), nullable=False)
    store = Column(String(24), nullable=False)
    environment = Column(String(16), nullable=False)
    # Derived only from the fetched snapshot, never from webhook payloads.
    subscription_key = Column(String(255), nullable=False)
    source_customer_id = Column(String(255), nullable=False)
    store_identifier = Column(String(255), nullable=True)
    original_transaction_id = Column(String(255), nullable=True)
    product_id = Column(String(255), nullable=False)
    # From the provider's own entitlement map; NULL = grants nothing.
    entitlement_key = Column(String(64), nullable=True)
    ownership_type = Column(String(16), nullable=True)
    period_type = Column(String(16), nullable=True)
    state = Column(String(16), nullable=False)
    will_renew = Column(Boolean, nullable=True)
    is_lifetime = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    purchased_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    grace_expires_at = Column(DateTime(timezone=True), nullable=True)
    unsubscribe_detected_at = Column(DateTime(timezone=True), nullable=True)
    billing_issue_detected_at = Column(DateTime(timezone=True), nullable=True)
    refunded_at = Column(DateTime(timezone=True), nullable=True)
    absent_since = Column(DateTime(timezone=True), nullable=True)
    # Snapshot time; an older snapshot never overwrites a newer one.
    fetched_at = Column(DateTime(timezone=True), nullable=False)
    last_synced_at = Column(DateTime(timezone=True), nullable=False)
    version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "provider", "environment", "subscription_key", name="uq_ss_key"
        ),
        CheckConstraint(
            "environment IN ('production', 'sandbox')", name="ck_ss_environment"
        ),
        CheckConstraint(
            "state IN ('trialing', 'active', 'in_grace', 'billing_retry', 'paused', "
            "'expired', 'refunded', 'revoked', 'absent')",
            name="ck_ss_state",
        ),
        Index("ix_ss_user", "user_id"),
        Index("ix_ss_customer", "provider", "source_customer_id"),
        Index("ix_ss_expiry", "expires_at"),
    )
