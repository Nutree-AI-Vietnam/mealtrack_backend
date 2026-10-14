"""Add ai_jobs and real push token registration.

Revision ID: 20261013115453616929
Revises: 20261013020801296576
Create Date: 2026-10-13 18:54:53.637433

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261013115453616929"
down_revision: str | None = "20261013020801296576"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "ai_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("job_type", sa.String(length=16), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default="queued", nullable=False
        ),
        sa.Column("language", sa.String(length=8), server_default="en", nullable=False),
        sa.Column("request_payload", _JSON, nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=True),
        sa.Column("result_payload", _JSON, nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_kind", sa.String(length=16), nullable=True),
        sa.Column("error_retryable", sa.Boolean(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="4", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claim_token", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notify_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notify_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "error_kind IS NULL OR error_kind IN ('transient', 'permanent')",
            name="ck_ai_jobs_error_kind",
        ),
        sa.CheckConstraint(
            "job_type IN ('chat', 'meal_scan', 'parse_text')",
            name="ck_ai_jobs_job_type",
        ),
        sa.CheckConstraint(
            "status <> 'running' OR "
            "(claim_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_ai_jobs_running_lease",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_ai_jobs_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts >= 1 AND attempts <= max_attempts",
            name="ck_ai_jobs_attempts",
        ),
        sa.CheckConstraint("notify_attempts >= 0", name="ck_ai_jobs_notify_attempts"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_jobs_claimable",
        "ai_jobs",
        ["available_at", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_ai_jobs_notify_due",
        "ai_jobs",
        ["notify_after"],
        unique=False,
        postgresql_where=sa.text("notified_at IS NULL AND notify_after IS NOT NULL"),
    )
    op.create_index(
        "ix_ai_jobs_user_created", "ai_jobs", ["user_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_ai_jobs_user_dedupe",
        "ai_jobs",
        ["user_id", "dedupe_key"],
        unique=False,
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
    )

    # Tokens written before this revision came from a retired sender and are
    # never targeted; only rows registered from now on get last_registered_at.
    op.add_column(
        "user_fcm_tokens",
        sa.Column("last_registered_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Widening a varchar is a catalog-only change in PostgreSQL (no rewrite).
    op.alter_column(
        "user_fcm_tokens",
        "fcm_token",
        existing_type=sa.VARCHAR(length=255),
        type_=sa.String(length=1024),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Tokens longer than the old column cannot be kept; clients re-register.
    op.execute("DELETE FROM user_fcm_tokens WHERE length(fcm_token) > 255")
    op.alter_column(
        "user_fcm_tokens",
        "fcm_token",
        existing_type=sa.String(length=1024),
        type_=sa.VARCHAR(length=255),
        existing_nullable=False,
    )
    op.drop_column("user_fcm_tokens", "last_registered_at")

    op.drop_index(
        "ix_ai_jobs_user_dedupe",
        table_name="ai_jobs",
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
    )
    op.drop_index("ix_ai_jobs_user_created", table_name="ai_jobs")
    op.drop_index(
        "ix_ai_jobs_notify_due",
        table_name="ai_jobs",
        postgresql_where=sa.text("notified_at IS NULL AND notify_after IS NOT NULL"),
    )
    op.drop_index(
        "ix_ai_jobs_claimable",
        table_name="ai_jobs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_table("ai_jobs")
