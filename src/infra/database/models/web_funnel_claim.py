"""Persistence models for the dark-launched paid web claim aggregate."""

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

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.base import Base
from src.infra.database.models.base import BaseMixin


class WebFunnelLead(Base, BaseMixin):
    """Pre-checkout, possession-bound onboarding snapshot; never an entitlement."""

    __tablename__ = "web_funnel_leads"

    email = Column(String(255), nullable=False)
    access_key_hash = Column(String(64), nullable=False)
    request_id = Column(String(128), nullable=False, unique=True)
    snapshot_version = Column(
        String(64), nullable=False, default="web_onboarding_snapshot_v1"
    )
    snapshot = Column(JSON, nullable=False)
    snapshot_hash = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="draft")
    revision = Column(Integer, nullable=False, default=1)
    payment_verified_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    claimed_uid = Column(String(128), nullable=True)
    access_sync_status = Column(String(16), nullable=False, default="pending")

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'checkout_started', 'payment_verified', "
            "'email_queued', 'claim_reserved', 'claimed', 'expired', 'revoked', "
            "'conflict', 'refunded')",
            name="ck_web_funnel_lead_status",
        ),
        CheckConstraint(
            "access_sync_status IN ('active', 'pending', 'refunded')",
            name="ck_web_funnel_lead_access_sync_status",
        ),
        Index("ix_web_funnel_leads_access_key_hash", "access_key_hash"),
        Index("ix_web_funnel_leads_status", "status"),
        Index("ix_web_funnel_leads_claimed_uid", "claimed_uid"),
    )


class WebFunnelClaim(Base, BaseMixin):
    """Hashed, single-use magic credential and its bounded exchange reservation."""

    __tablename__ = "web_funnel_claims"

    lead_id = Column(
        String(36),
        ForeignKey("web_funnel_leads.id", ondelete="CASCADE"),
        nullable=False,
    )
    generation = Column(Integer, nullable=False)
    magic_token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    reservation_retry_secret_hash = Column(String(64), nullable=True)
    reservation_id = Column(String(36), nullable=True, unique=True)
    reservation_uid = Column(String(128), nullable=True)
    provisional_reservation_uid = Column(String(128), nullable=True)
    reservation_expires_at = Column(DateTime(timezone=True), nullable=True)
    exchange_token_hash = Column(String(64), nullable=True, unique=True)
    exchange_expires_at = Column(DateTime(timezone=True), nullable=True)
    consumed_uid = Column(String(128), nullable=True)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    result = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "lead_id", "generation", name="uq_web_funnel_claim_generation"
        ),
        Index("ix_web_funnel_claims_lead_id", "lead_id"),
        Index("ix_web_funnel_claims_reservation_uid", "reservation_uid"),
        Index("ix_web_funnel_claims_reservation_id", "reservation_id"),
        Index(
            "uq_web_funnel_claims_active_generation",
            "lead_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL AND consumed_at IS NULL"),
        ),
    )


class WebFunnelRedemption(Base, BaseMixin):
    """Verified anonymous web-customer binding and one-time app finalization."""

    __tablename__ = "web_funnel_redemptions"

    lead_id = Column(
        String(36),
        ForeignKey("web_funnel_leads.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    provider = Column(String(32), nullable=False, default="revenuecat")
    environment = Column(String(32), nullable=False)
    project = Column(String(128), nullable=False)
    original_app_user_id = Column(String(255), nullable=False)
    verified_app_user_id = Column(String(255), nullable=False)
    provider_app_user_ids = Column(JSON, nullable=True)
    entitlement_id = Column(String(128), nullable=False)
    product_id = Column(String(255), nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=False)
    finalized_uid = Column(String(128), nullable=True)
    finalized_at = Column(DateTime(timezone=True), nullable=True)
    redeemer_uid = Column(String(128), nullable=True)
    redemption_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    redemption_link_hash = Column(String(64), nullable=True, unique=True)
    preflight_token_hash = Column(String(64), nullable=True, unique=True)
    preflight_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    preflight_uid = Column(String(128), nullable=True)
    preflight_at = Column(DateTime(timezone=True), nullable=True)
    finalization_key_hash = Column(String(64), nullable=True, unique=True)
    result = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "project",
            "environment",
            "original_app_user_id",
            name="uq_web_funnel_redemptions_provider_customer",
        ),
        Index("ix_web_funnel_redemptions_finalized_uid", "finalized_uid"),
        Index("ix_web_funnel_redemptions_redeemer_uid", "redeemer_uid"),
        Index(
            "ix_web_funnel_redemptions_preflight_uid_hash",
            "preflight_uid",
            "redemption_link_hash",
        ),
    )


class WebFunnelProviderEvent(Base):
    """Durable inbox for provider wake-up signals; payload is never returned to clients."""

    __tablename__ = "web_funnel_provider_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider_event_id = Column(String(255), nullable=False, unique=True)
    event_type = Column(String(64), nullable=False)
    lead_id = Column(String(36), nullable=True)
    payload = Column(JSON, nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class WebFunnelOutbox(Base):
    """External email/RevenueCat work inserted atomically with claim state."""

    __tablename__ = "web_funnel_outbox"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    idempotency_key = Column(String(255), nullable=False, unique=True)
    job_type = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_web_funnel_outbox_status",
        ),
        Index("ix_web_funnel_outbox_due", "status", "next_attempt_at"),
    )
