"""Device push tokens (FCM registration tokens) per user."""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    func,
    text,
)

from src.infra.database.base import Base

# Matches the table created before this ORM existed (varchar token). Migration
# 008 gave it naive timestamps that default to now(), but databases built by
# create_all from the model removed in August, staging among them, have
# timestamptz columns with no default. So the Python-side defaults put both
# timestamps in every INSERT. Only rows with last_registered_at were registered
# by this API and are safe to push to.
FCM_TOKEN_MAX_LENGTH = 1024


class UserFcmTokenORM(Base):
    __tablename__ = "user_fcm_tokens"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(
        DateTime(), nullable=False, default=func.now(), server_default=func.now()
    )
    updated_at = Column(
        DateTime(),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
    )
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    fcm_token = Column(String(FCM_TOKEN_MAX_LENGTH), nullable=False, unique=True)
    device_type = Column(
        Enum("ios", "android", name="device_type_enum"), nullable=False
    )
    is_active = Column(Boolean, nullable=False, default=True)
    last_registered_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_user_fcm_tokens_user_id", "user_id"),
        Index(
            "idx_user_fcm_tokens_active",
            "is_active",
            postgresql_where=text("is_active = true"),
        ),
        Index("idx_user_fcm_tokens_user_active", "user_id", "is_active"),
    )
