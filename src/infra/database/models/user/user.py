"""
Core user model for authentication and account management.
"""

from sqlalchemy import Boolean, Column, DateTime, Enum, Index, String, Text
from sqlalchemy.orm import relationship

from src.api.schemas.common.auth_enums import AuthProviderEnum
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.base import Base
from src.infra.database.models.base import BaseMixin


class User(Base, BaseMixin):
    """Core user table for authentication and account management."""

    __tablename__ = "users"

    # Firebase Integration
    firebase_uid = Column(String(128), unique=True, nullable=False, index=True)
    revenuecat_customer_id = Column(String(255), unique=True, nullable=True)

    # Basic Information
    email = Column(String(255), unique=True, nullable=False)
    username = Column(String(100), unique=True, nullable=False)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)

    # Authentication & OAuth
    password_hash = Column(String(255), nullable=False)
    phone_number = Column(String(20), nullable=True)
    display_name = Column(String(100), nullable=True)
    photo_url = Column(Text, nullable=True)
    provider = Column(
        Enum(AuthProviderEnum, native_enum=False, length=32),
        nullable=False,
        default=AuthProviderEnum.GOOGLE,
    )  # google, apple, email_link, anonymous — VARCHAR must fit EMAIL_LINK

    # Status & Activity
    is_active = Column(Boolean, default=True, nullable=False)
    onboarding_completed = Column(Boolean, default=False, nullable=False)
    last_accessed = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # Timezone (IANA format, e.g., "America/Los_Angeles")
    timezone = Column(String(50), nullable=False, server_default="UTC")

    # Preferred language (ISO 639-1: 'en', 'vi', 'es', 'fr', 'de', 'ja', 'zh')
    language_code = Column(String(5), nullable=False, default="en", server_default="en")

    # Email preferences
    welcome_email_sent_at = Column(DateTime(timezone=True), nullable=True)
    email_opt_out = Column(Boolean, default=False, nullable=False)

    # Indexes for performance
    __table_args__ = (
        Index("idx_firebase_uid", "firebase_uid"),
        Index("idx_provider", "provider"),
        Index("idx_onboarding_completed", "onboarding_completed"),
        Index("idx_users_timezone", "timezone"),
    )

    # Relationships
    profiles = relationship(
        "UserProfile", back_populates="user", cascade="all, delete-orphan", lazy="raise"
    )
    subscriptions = relationship(
        "Subscription",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    referral_code = relationship("ReferralCode", back_populates="user", uselist=False)
    referral_wallet = relationship(
        "ReferralWallet", back_populates="user", uselist=False
    )

    @property
    def current_profile(self):
        """Get the current active profile."""
        return next((p for p in self.profiles if p.is_current), None)

    def get_active_subscription(self):
        """Get user's active subscription, if any."""
        for subscription in self.subscriptions:
            if subscription.is_active():
                return subscription
        return None

    def has_active_subscription(self) -> bool:
        """
        Check if user has active subscription.

        Note: This checks local cache. For real-time validation,
        use RevenueCat API via the RevenueCatService.
        """
        return self.get_active_subscription() is not None
