"""Server-sent push outbox: the ``notifications`` table.

Mirrors the table exactly as migrations 050, 20260609000005 and
20260609000006 built it (names, partial-index predicates and all), so
autogenerate reports no difference for it.
"""

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from src.infra.database.base import Base

_JSON = JSON().with_variant(JSONB(), "postgresql")


class NotificationOutboxORM(Base):
    __tablename__ = "notifications"

    id = Column(String(36), primary_key=True)
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    notification_type = Column(String(30), nullable=False)
    # The user's local date at scheduled_for_utc; part of the dedupe key.
    scheduled_date = Column(Date, nullable=False)
    scheduled_for_utc = Column(DateTime(timezone=True), nullable=False)
    # pending -> processing -> sent | failed; pending or processing -> expired
    status = Column(
        String(10), nullable=False, default="pending", server_default="pending"
    )
    # Immutable render snapshot; recipients come from user_fcm_tokens.
    context = Column(_JSON, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    context_schema_version = Column(
        Integer, nullable=False, default=1, server_default="1"
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "notification_type",
            "scheduled_date",
            name="uq_notification_per_user_type_date",
        ),
        Index(
            "idx_notifications_due",
            "scheduled_for_utc",
            "status",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "idx_notifications_expires",
            "expires_at",
            postgresql_where=text("status != 'pending'"),
        ),
        Index(
            "idx_notifications_user_status_date",
            "user_id",
            "status",
            "scheduled_date",
        ),
        Index(
            "idx_notifications_processing_reclaim",
            "scheduled_for_utc",
            postgresql_where=text("status = 'processing'"),
        ),
    )
