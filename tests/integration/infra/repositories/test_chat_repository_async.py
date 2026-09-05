"""PostgreSQL tests for chat lease fencing, retrieval tags, and concurrency."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from src.api.schemas.common.auth_enums import AuthProviderEnum
from src.domain.model.chat import ChatMessageStatus, ChatUsage
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.chat import (
    ChatKnowledgeChunkORM,
    ChatKnowledgeDocumentORM,
)
from src.infra.database.models.user.user import User
from src.infra.repositories.chat_repository_async import AsyncChatRepository


async def _insert_user(session, suffix: str) -> User:
    user = User(
        id=str(uuid.uuid4()),
        firebase_uid=f"chat-{suffix}",
        email=f"chat-{suffix}@test.com",
        username=f"chat_{suffix}",
        password_hash="hashed_password",
        provider=AuthProviderEnum.GOOGLE,
        is_active=True,
        onboarding_completed=False,
        last_accessed=utc_now(),
    )
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_complete_is_fenced_to_generation_token(async_db_session):
    user = await _insert_user(async_db_session, "fence")
    repo = AsyncChatRepository(async_db_session)
    lease = utc_now() + timedelta(seconds=90)
    claim = await repo.claim_turn(
        user_id=user.id,
        content="How much is left?",
        idempotency_key="key-fence",
        request_fingerprint="abc",
        lease_expires_at=lease,
    )
    stale = await repo.complete_assistant_message(
        message_id=claim.assistant_message.id,
        content="stale",
        model="gpt-5.6-luna",
        usage=ChatUsage(),
        prompt_version="chat_prompt_v3",
        context_version="chat_context_v1",
        citation_source_keys=(),
        provider_response_id=None,
        generation_id="not-the-token",
    )
    assert stale is None
    completed = await repo.complete_assistant_message(
        message_id=claim.assistant_message.id,
        content="Nutree has 650 remaining.",
        model="gpt-5.6-luna",
        usage=ChatUsage(input_tokens=1, output_tokens=1),
        prompt_version="chat_prompt_v3",
        context_version="chat_context_v1",
        citation_source_keys=(),
        provider_response_id=None,
        generation_id=claim.assistant_message.generation_id,
    )
    assert completed is not None
    assert completed.content == "Nutree has 650 remaining."
    assert completed.status == ChatMessageStatus.COMPLETED


@pytest.mark.asyncio
async def test_expired_generation_cannot_complete_after_replacement(async_db_session):
    user = await _insert_user(async_db_session, "lease")
    repo = AsyncChatRepository(async_db_session)
    first = await repo.claim_turn(
        user_id=user.id,
        content="First",
        idempotency_key="key-lease",
        request_fingerprint="abc",
        lease_expires_at=utc_now() - timedelta(seconds=1),
    )
    second = await repo.claim_turn(
        user_id=user.id,
        content="First",
        idempotency_key="key-lease",
        request_fingerprint="abc",
        lease_expires_at=utc_now() + timedelta(seconds=90),
    )
    assert first.assistant_message.id != second.assistant_message.id
    stale = await repo.complete_assistant_message(
        message_id=first.assistant_message.id,
        content="late first",
        model="gpt-5.6-luna",
        usage=ChatUsage(),
        prompt_version="chat_prompt_v3",
        context_version="chat_context_v1",
        citation_source_keys=(),
        provider_response_id=None,
        generation_id=first.assistant_message.generation_id,
    )
    assert stale is None
    winner = await repo.complete_assistant_message(
        message_id=second.assistant_message.id,
        content="replacement",
        model="gpt-5.6-luna",
        usage=ChatUsage(),
        prompt_version="chat_prompt_v3",
        context_version="chat_context_v1",
        citation_source_keys=(),
        provider_response_id=None,
        generation_id=second.assistant_message.generation_id,
    )
    assert winner is not None
    assert winner.content == "replacement"


@pytest.mark.asyncio
async def test_quota_count_excludes_replay_key(async_db_session):
    user = await _insert_user(async_db_session, "quota")
    repo = AsyncChatRepository(async_db_session)
    await repo.claim_turn(
        user_id=user.id,
        content="Hi",
        idempotency_key="key-40",
        request_fingerprint="abc",
        lease_expires_at=utc_now() + timedelta(seconds=90),
    )
    used = await repo.count_user_turns_since(
        user_id=user.id,
        since=utc_now().replace(hour=0, minute=0, second=0, microsecond=0),
    )
    excluded = await repo.count_user_turns_since(
        user_id=user.id,
        since=utc_now().replace(hour=0, minute=0, second=0, microsecond=0),
        exclude_idempotency_key="key-40",
    )
    assert used == 1
    assert excluded == 0


@pytest.mark.asyncio
async def test_knowledge_safety_tags_round_trip(async_db_session):
    now = utc_now()
    document = ChatKnowledgeDocumentORM(
        id=str(uuid.uuid4()),
        source_key="peanut-sauce",
        title="Peanut sauce",
        locale="en",
        content_version="1",
        content_sha256="abc",
        reviewer_id="reviewer",
        approved_at=now,
        safety_tags=["contains:peanut"],
        topic_tags=["recipe"],
        audience_tags=["general"],
        active=True,
        created_at=now,
        updated_at=now,
    )
    async_db_session.add(document)
    await async_db_session.flush()
    async_db_session.add(
        ChatKnowledgeChunkORM(
            id=str(uuid.uuid4()),
            document_id=document.id,
            chunk_index=0,
            content="A peanut sauce bowl.",
            token_count=4,
            created_at=now,
            updated_at=now,
        )
    )
    await async_db_session.flush()
    loaded = await async_db_session.execute(
        select(ChatKnowledgeDocumentORM).where(
            ChatKnowledgeDocumentORM.source_key == "peanut-sauce"
        )
    )
    row = loaded.scalar_one()
    assert list(row.safety_tags) == ["contains:peanut"]
