"""ORM model for user FCM push tokens."""

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, String

from src.infra.database.base import Base
from src.infra.database.models.base import BaseMixin


class UserFcmTokenORM(Base, BaseMixin):
    """user_fcm_tokens – one FCM registration token per device.

    Note: this table was created via Alembic migration 008 before the ORM
    model existed, so the database column for ``created_at`` may lack a
    ``DEFAULT now()`` clause on existing instances.  Always supply
    ``created_at`` explicitly when using raw ``pg_insert`` statements.
    """

    __tablename__ = "user_fcm_tokens"

    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fcm_token = Column(String(255), nullable=False, unique=True)
    device_type = Column(
        Enum("ios", "android", name="device_type_enum"),
        nullable=False,
    )
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    last_registered_at = Column(DateTime(timezone=True), nullable=True)
