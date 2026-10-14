"""Background AI jobs (meal scan, parse-text, chat) and their push delivery state."""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from src.infra.database.base import Base

_JSON = JSON().with_variant(JSONB(), "postgresql")


class AiJobORM(Base):
    __tablename__ = "ai_jobs"

    # Minted by the client so a lost response can be recovered by id.
    id = Column(String(36), primary_key=True)
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    job_type = Column(String(16), nullable=False)
    status = Column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    language = Column(String(8), nullable=False, default="en", server_default="en")
    request_payload = Column(_JSON, nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    # Chat jobs: the turn's Idempotency-Key, so GET /v1/chat can find the job.
    dedupe_key = Column(String(128), nullable=True)
    result_payload = Column(_JSON, nullable=True)
    error_code = Column(String(64), nullable=True)
    error_kind = Column(String(16), nullable=True)
    error_retryable = Column(Boolean, nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    max_attempts = Column(Integer, nullable=False, default=4, server_default="4")
    available_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    claim_token = Column(String(36), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    seen_at = Column(DateTime(timezone=True), nullable=True)
    # Push is due at this time unless the user has seen the result by then.
    notify_after = Column(DateTime(timezone=True), nullable=True)
    notified_at = Column(DateTime(timezone=True), nullable=True)
    notify_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Fetch server-set timestamps with RETURNING on flush; async sessions
    # cannot lazy-load the expired attributes afterwards.
    __mapper_args__ = {"eager_defaults": True}

    __table_args__ = (
        CheckConstraint(
            "job_type IN ('chat', 'meal_scan', 'parse_text')",
            name="ck_ai_jobs_job_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_ai_jobs_status",
        ),
        CheckConstraint(
            "error_kind IS NULL OR error_kind IN ('transient', 'permanent')",
            name="ck_ai_jobs_error_kind",
        ),
        CheckConstraint(
            "attempts >= 0 AND max_attempts >= 1 AND attempts <= max_attempts",
            name="ck_ai_jobs_attempts",
        ),
        CheckConstraint(
            "status <> 'running' OR "
            "(claim_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_ai_jobs_running_lease",
        ),
        CheckConstraint("notify_attempts >= 0", name="ck_ai_jobs_notify_attempts"),
        Index(
            "ix_ai_jobs_claimable",
            "available_at",
            "created_at",
            "id",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_ai_jobs_user_created", "user_id", "created_at"),
        Index(
            "ix_ai_jobs_user_dedupe",
            "user_id",
            "dedupe_key",
            postgresql_where=text("dedupe_key IS NOT NULL"),
        ),
        Index(
            "ix_ai_jobs_notify_due",
            "notify_after",
            postgresql_where=text("notified_at IS NULL AND notify_after IS NOT NULL"),
        ),
    )
