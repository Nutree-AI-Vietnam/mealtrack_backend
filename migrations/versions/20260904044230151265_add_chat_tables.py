"""Create Nutree-owned chat thread, message, and reviewed knowledge tables.

Revision ID: 20260904044230151265
Revises: 20260903000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "20260904044230151265"
down_revision: str | None = "20260903000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "chat_thread",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_through_message_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chat_thread_user_id", "chat_thread", ["user_id"], unique=True)

    op.create_table(
        "chat_message",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "thread_id",
            sa.String(length=36),
            sa.ForeignKey("chat_thread.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "in_reply_to_id",
            sa.String(length=36),
            sa.ForeignKey("chat_message.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("provider_response_id", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("context_version", sa.String(length=32), nullable=True),
        sa.Column(
            "citation_source_keys",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "generation_lease_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("generation_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name="ck_chat_message_role"
        ),
        sa.CheckConstraint(
            "status IN ('generating', 'completed', 'failed')",
            name="ck_chat_message_status",
        ),
    )
    op.create_index(
        "ix_chat_message_thread_created",
        "chat_message",
        ["thread_id", "created_at"],
    )
    op.create_index(
        "uq_chat_message_user_idempotency",
        "chat_message",
        ["thread_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("role = 'user' AND idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "uq_chat_one_generating_assistant",
        "chat_message",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text("role = 'assistant' AND status = 'generating'"),
    )
    op.create_foreign_key(
        "fk_chat_thread_summary_message",
        "chat_thread",
        "chat_message",
        ["summary_through_message_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "chat_knowledge_document",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("canonical_uri", sa.Text(), nullable=True),
        sa.Column("content_version", sa.String(length=32), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("reviewer_id", sa.String(length=128), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "safety_tags",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "topic_tags",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "audience_tags",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_key", name="uq_chat_knowledge_document_source_key"),
    )
    op.create_index(
        "ix_chat_knowledge_document_locale_active",
        "chat_knowledge_document",
        ["locale", "active"],
    )

    op.create_table(
        "chat_knowledge_chunk",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(length=36),
            sa.ForeignKey("chat_knowledge_document.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tsv", postgresql.TSVECTOR(), nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_chat_knowledge_chunk_document_index",
        ),
    )
    op.create_index(
        "ix_chat_knowledge_chunk_document_id",
        "chat_knowledge_chunk",
        ["document_id"],
    )
    op.create_index(
        "ix_chat_knowledge_chunk_tsv",
        "chat_knowledge_chunk",
        ["tsv"],
        postgresql_using="gin",
    )
    op.execute(
        "CREATE INDEX ix_chat_knowledge_chunk_embedding "
        "ON chat_knowledge_chunk USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION chat_knowledge_chunk_tsv_refresh()
        RETURNS trigger AS $$
        BEGIN
            NEW.tsv := to_tsvector('simple', coalesce(NEW.content, ''));
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_chat_knowledge_chunk_tsv
        BEFORE INSERT OR UPDATE OF content ON chat_knowledge_chunk
        FOR EACH ROW EXECUTE PROCEDURE chat_knowledge_chunk_tsv_refresh()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_chat_knowledge_chunk_tsv ON chat_knowledge_chunk"
    )
    op.execute("DROP FUNCTION IF EXISTS chat_knowledge_chunk_tsv_refresh()")
    op.execute("DROP INDEX IF EXISTS ix_chat_knowledge_chunk_embedding")
    op.drop_index("ix_chat_knowledge_chunk_tsv", table_name="chat_knowledge_chunk")
    op.drop_index(
        "ix_chat_knowledge_chunk_document_id", table_name="chat_knowledge_chunk"
    )
    op.drop_table("chat_knowledge_chunk")
    op.drop_index(
        "ix_chat_knowledge_document_locale_active",
        table_name="chat_knowledge_document",
    )
    op.drop_table("chat_knowledge_document")
    op.drop_constraint(
        "fk_chat_thread_summary_message", "chat_thread", type_="foreignkey"
    )
    op.drop_index("uq_chat_one_generating_assistant", table_name="chat_message")
    op.drop_index("uq_chat_message_user_idempotency", table_name="chat_message")
    op.drop_index("ix_chat_message_thread_created", table_name="chat_message")
    op.drop_table("chat_message")
    op.drop_index("ix_chat_thread_user_id", table_name="chat_thread")
    op.drop_table("chat_thread")
