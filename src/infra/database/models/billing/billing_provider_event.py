"""Webhook inbox that doubles as the entitlement sync job queue."""

import uuid

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from src.infra.database.base import Base


class BillingProviderEventORM(Base):
    """One provider webhook delivery, deduplicated on ``provider_event_id``."""

    __tablename__ = "billing_provider_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider = Column(String(32), nullable=False)
    # RC event.id; 'sha256:'||hex(sha256(raw body)) when the event has none.
    provider_event_id = Column(String(255), nullable=False)
    event_type = Column(String(64), nullable=False)
    environment = Column(String(16), nullable=True)
    app_user_id = Column(String(255), nullable=True)
    # event_timestamp_ms: audit only, never used for ordering.
    event_ts = Column(DateTime(timezone=True), nullable=True)
    # subscriber_attributes stripped before insert (D9). none_as_null: a purge
    # writes SQL NULL, not JSON 'null', so ``raw IS NOT NULL`` means "has a body".
    raw = Column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
        nullable=True,
    )
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    status = Column(String(16), nullable=False)
    park_reason = Column(String(32), nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    lease_owner = Column(String(64), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_bpe_event"),
        CheckConstraint(
            "status IN ('received', 'processing', 'processed', 'unmatched', "
            "'retry', 'ignored', 'dead')",
            name="ck_bpe_status",
        ),
        Index(
            "ix_bpe_due",
            "next_attempt_at",
            postgresql_where=text("status IN ('received', 'retry')"),
        ),
        Index(
            "ix_bpe_unmatched",
            "received_at",
            postgresql_where=text("status = 'unmatched'"),
        ),
        Index("ix_bpe_user", "user_id", "received_at"),
        # Crashed-worker reclaim: claim_due's second query.
        Index(
            "ix_bpe_lease_expiry",
            "lease_expires_at",
            postgresql_where=text("status = 'processing'"),
        ),
        # /v1/users/sync replays the user's parked rows by provider id.
        Index(
            "ix_bpe_unmatched_app_user",
            "app_user_id",
            postgresql_where=text("status = 'unmatched'"),
        ),
        # D9 retention: the worker nulls bodies older than 90 days.
        Index(
            "ix_bpe_raw_retention",
            "received_at",
            postgresql_where=text("raw IS NOT NULL"),
        ),
    )
