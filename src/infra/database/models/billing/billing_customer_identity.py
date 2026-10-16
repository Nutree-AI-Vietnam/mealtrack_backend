"""One user, many billing-provider customer ids."""

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.base import Base


class BillingCustomerIdentityORM(Base):
    """Replaces the write-only ``users.revenuecat_customer_id``.

    Alias rows only route re-syncs; they never grant access.
    """

    __tablename__ = "billing_customer_identities"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    provider = Column(String(32), nullable=False)
    project = Column(
        String(64), nullable=False, default="default", server_default="default"
    )
    provider_customer_id = Column(String(255), nullable=False)
    kind = Column(String(16), nullable=False)
    # Set on account deletion (D11); a retired primary frees the user's slot.
    retired_at = Column(DateTime(timezone=True), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "project",
            "provider_customer_id",
            name="uq_bci_provider_customer",
        ),
        CheckConstraint(
            "kind IN ('primary', 'alias', 'anonymous', 'legacy_lead')",
            name="ck_bci_kind",
        ),
        Index(
            "uq_bci_one_primary",
            "user_id",
            "provider",
            "project",
            unique=True,
            postgresql_where=text("kind = 'primary' AND retired_at IS NULL"),
        ),
        Index("ix_bci_user", "user_id"),
    )
