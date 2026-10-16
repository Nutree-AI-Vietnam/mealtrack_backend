"""Entitlement projection: O(1) reads for the app and server gates."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    String,
    text,
)

from src.infra.database.base import Base


class UserEntitlementORM(Base):
    """Written only by the entitlement sync service.

    ``has_access`` is a cache as of ``computed_at``; readers re-check
    ``access_until`` and recompute once ``next_change_at`` has passed.
    """

    __tablename__ = "user_entitlements"

    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    entitlement_key = Column(String(64), nullable=False)
    has_access = Column(Boolean, nullable=False)
    state = Column(String(20), nullable=False)
    access_until = Column(DateTime(timezone=True), nullable=True)
    is_lifetime = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    next_change_at = Column(DateTime(timezone=True), nullable=True)
    will_renew = Column(Boolean, nullable=True)
    period_type = Column(String(16), nullable=True)
    primary_source = Column(String(8), nullable=False)
    primary_ref = Column(String(255), nullable=True)
    product_id = Column(String(255), nullable=True)
    store = Column(String(24), nullable=True)
    is_sandbox = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    ever_had_access = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    version = Column(BigInteger, nullable=False)
    computed_at = Column(DateTime(timezone=True), nullable=False)
    last_provider_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_purchase_hint_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("user_id", "entitlement_key", name="pk_user_entitlements"),
        CheckConstraint(
            "state IN ('trial', 'active', 'grace', 'cancelled_active', 'expired', "
            "'none')",
            name="ck_ue_state",
        ),
        CheckConstraint(
            "primary_source IN ('store', 'grant')", name="ck_ue_primary_source"
        ),
        Index("ix_ue_next_change", "next_change_at"),
    )
