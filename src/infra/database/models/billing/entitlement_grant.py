"""Manual entitlement grants from our own ledger (D5)."""

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.base import Base


class EntitlementGrantORM(Base):
    """Append-only, revoke-only. Store-derived access is never copied here."""

    __tablename__ = "entitlement_grants"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    entitlement_key = Column(
        String(64), nullable=False, default="standard", server_default="standard"
    )
    source = Column(String(16), nullable=False)
    source_ref = Column(String(255), nullable=True)
    reason = Column(String(255), nullable=False)
    starts_at = Column(DateTime(timezone=True), nullable=False)
    ends_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoke_reason = Column(String(255), nullable=True)
    # Admin email that revoked the grant (D7), for the audit trail.
    revoked_by = Column(String(255), nullable=True)
    # Admin email from the hardened require_admin (D7).
    created_by = Column(String(255), nullable=False)
    # RC promotional grant id while mirrored (D5); unused in NM-680.
    rc_mirror_ref = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "source IN ('admin', 'support', 'qa', 'migration')",
            name="ck_eg_source",
        ),
        CheckConstraint("ends_at > starts_at", name="ck_eg_window_positive"),
        # No lifetime grants in NM-680 (D5). Postgres interval syntax, so the
        # SQLite metadata builds used by unit tests skip it.
        CheckConstraint(
            "ends_at <= starts_at + interval '366 days'", name="ck_eg_window_max"
        ).ddl_if(dialect="postgresql"),
        Index(
            "uq_eg_source",
            "source",
            "source_ref",
            "entitlement_key",
            unique=True,
            postgresql_where=text("source_ref IS NOT NULL"),
        ),
        Index(
            "ix_eg_user_live",
            "user_id",
            "entitlement_key",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )
