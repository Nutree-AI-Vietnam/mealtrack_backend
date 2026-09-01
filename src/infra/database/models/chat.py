"""ORM models for Nutree-owned chat threads, messages, and reviewed knowledge."""

from __future__ import annotations

from pgvector.sqlalchemy import Vector
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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import relationship

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.base import Base
from src.infra.database.models.base import BaseMixin

CHAT_EMBEDDING_DIMENSIONS = 1536
_StringArray = ARRAY(String).with_variant(JSON(), "sqlite")
_TSVector = TSVECTOR().with_variant(Text(), "sqlite")


class ChatThreadORM(Base, BaseMixin):
    __tablename__ = "chat_thread"

    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    summary = Column(Text, nullable=True)
    summary_through_message_id = Column(
        String(36),
        ForeignKey(
            "chat_message.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_chat_thread_summary_message",
        ),
        nullable=True,
    )

    messages = relationship(
        "ChatMessageORM",
        back_populates="thread",
        cascade="all, delete-orphan",
        foreign_keys="ChatMessageORM.thread_id",
        lazy="raise",
    )

    __table_args__ = (Index("ix_chat_thread_user_id", "user_id", unique=True),)


class ChatMessageORM(Base, BaseMixin):
    __tablename__ = "chat_message"

    thread_id = Column(
        String(36),
        ForeignKey("chat_thread.id", ondelete="CASCADE"),
        nullable=False,
    )
    role = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False)
    content = Column(Text, nullable=True)
    idempotency_key = Column(String(160), nullable=True)
    request_fingerprint = Column(String(64), nullable=True)
    in_reply_to_id = Column(
        String(36),
        ForeignKey("chat_message.id", ondelete="SET NULL"),
        nullable=True,
    )
    model = Column(String(80), nullable=True)
    provider_response_id = Column(String(128), nullable=True)
    prompt_version = Column(String(32), nullable=True)
    context_version = Column(String(32), nullable=True)
    citation_source_keys = Column(_StringArray, nullable=False, default=list)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    cached_tokens = Column(Integer, nullable=True)
    generation_lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(64), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    thread = relationship(
        "ChatThreadORM",
        back_populates="messages",
        foreign_keys=[thread_id],
        lazy="raise",
    )

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_message_role"),
        CheckConstraint(
            "status IN ('generating', 'completed', 'failed')",
            name="ck_chat_message_status",
        ),
        Index("ix_chat_message_thread_created", "thread_id", "created_at"),
        Index(
            "uq_chat_message_user_idempotency",
            "thread_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("role = 'user' AND idempotency_key IS NOT NULL"),
        ),
        Index(
            "uq_chat_one_generating_assistant",
            "thread_id",
            unique=True,
            postgresql_where=text("role = 'assistant' AND status = 'generating'"),
        ),
    )


class ChatKnowledgeDocumentORM(Base, BaseMixin):
    __tablename__ = "chat_knowledge_document"

    source_key = Column(String(128), nullable=False, unique=True)
    title = Column(String(255), nullable=False)
    locale = Column(String(8), nullable=False)
    canonical_uri = Column(Text, nullable=True)
    content_version = Column(String(32), nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    reviewer_id = Column(String(128), nullable=False)
    approved_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    safety_tags = Column(_StringArray, nullable=False, default=list)
    topic_tags = Column(_StringArray, nullable=False, default=list)
    audience_tags = Column(_StringArray, nullable=False, default=list)
    active = Column(Boolean, nullable=False, server_default="true")

    chunks = relationship(
        "ChatKnowledgeChunkORM",
        back_populates="document",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        Index("ix_chat_knowledge_document_locale_active", "locale", "active"),
    )


class ChatKnowledgeChunkORM(Base, BaseMixin):
    __tablename__ = "chat_knowledge_chunk"

    document_id = Column(
        String(36),
        ForeignKey("chat_knowledge_document.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    token_count = Column(Integer, nullable=False, default=0)
    tsv = Column(_TSVector, nullable=True)
    embedding = Column(Vector(CHAT_EMBEDDING_DIMENSIONS), nullable=True)

    document = relationship(
        "ChatKnowledgeDocumentORM",
        back_populates="chunks",
        lazy="raise",
    )

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_chat_knowledge_chunk_document_index",
        ),
        Index("ix_chat_knowledge_chunk_document_id", "document_id"),
        Index(
            "ix_chat_knowledge_chunk_tsv",
            "tsv",
            postgresql_using="gin",
        ),
    )
