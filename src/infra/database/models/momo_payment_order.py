"""Persistence for MoMo one-time premium pass orders."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)

from src.infra.database.base import Base
from src.infra.database.models.base import BaseMixin


class MomoPaymentOrder(Base, BaseMixin):
    """One MoMo payment for one pass, bought in the app or on the quiz web."""

    __tablename__ = "momo_payment_orders"

    order_id = Column(String(64), nullable=False, unique=True)
    request_id = Column(String(64), nullable=False)
    channel = Column(String(16), nullable=False)
    request_type = Column(String(32), nullable=False)
    plan = Column(String(16), nullable=False)
    weeks = Column(Integer, nullable=False)
    amount = Column(Integer, nullable=False)
    intro_price = Column(Boolean, nullable=False, default=False)
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    lead_id = Column(
        String(36),
        ForeignKey("web_funnel_leads.id", ondelete="SET NULL"),
        nullable=True,
    )
    buyer_email = Column(String(255), nullable=True)
    status = Column(String(16), nullable=False, default="pending")
    result_code = Column(Integer, nullable=True)
    result_message = Column(String(255), nullable=True)
    trans_id = Column(BigInteger, nullable=True)
    pay_type = Column(String(32), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    # Entitlement: who received the pass and the window RevenueCat grants.
    granted_user_id = Column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    access_starts_at = Column(DateTime(timezone=True), nullable=True)
    access_ends_at = Column(DateTime(timezone=True), nullable=True)
    granted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'paid', 'failed', 'expired')",
            name="ck_momo_payment_orders_status",
        ),
        CheckConstraint(
            "channel IN ('app', 'web_funnel')",
            name="ck_momo_payment_orders_channel",
        ),
        Index("ix_momo_payment_orders_user_id", "user_id"),
        Index("ix_momo_payment_orders_lead_id", "lead_id"),
        Index("ix_momo_payment_orders_buyer_email", "buyer_email"),
        Index("ix_momo_payment_orders_granted_user_id", "granted_user_id"),
    )
