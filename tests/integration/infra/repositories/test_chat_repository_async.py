"""PostgreSQL tests for chat threads, lease fencing, retrieval tags, and concurrency."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from src.api.schemas.common.auth_enums import AuthProviderEnum
from src.domain.exceptions.chat_exceptions import ChatThreadNotFoundError
from src.domain.model.chat import ChatMessageStatus, ChatUsage
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.chat import (
    ChatKnowledgeChunkORM,
    ChatKnowledgeDocumentORM,
    ChatThreadORM,
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


async def _complete_turn(repo, user_id, *, content, reply, key, thread_id=None):
    claim = await repo.claim_turn(
        user_id=user_id,
        content=content,
        idempotency_key=key,
        request_fingerprint=f"fp-{key}",
        lease_expires_at=utc_now() + timedelta(seconds=90),
        thread_id=thread_id,
    )
    await repo.complete_assistant_message(
        message_id=claim.assistant_message.id,
        content=reply,
        model="gpt-5.6-luna",
        usage=ChatUsage(),
        prompt_version="chat_prompt_v6",
        context_version="chat_context_v1",
        citation_source_keys=(),
        provider_response_id=None,
        generation_id=claim.assistant_message.generation_id,
    )
    return claim


@pytest.mark.asyncio
async def test_new_thread_keeps_prior_threads_listed(async_db_session):
    user = await _insert_user(async_db_session, "multi")
    repo = AsyncChatRepository(async_db_session)
    first = await _complete_turn(
        repo,
        user.id,
        content="What should I   eat for dinner?",
        reply="**Grilled fish** with rice fits your budget.",
        key="multi-1",
    )

    second = await repo.create_thread(user.id)
    assert second.id != first.thread.id
    # A blank thread is reused rather than piling up.
    assert (await repo.create_thread(user.id)).id == second.id
    # Blank threads stay out of the list until they have a message.
    listed = await repo.list_threads(user_id=user.id, limit=10)
    assert [item.thread.id for item in listed] == [first.thread.id]

    await _complete_turn(
        repo,
        user.id,
        content="Is oatmeal a good breakfast?",
        reply="Yes, add some protein.",
        key="multi-2",
        thread_id=second.id,
    )
    listed = await repo.list_threads(user_id=user.id, limit=10)
    assert [item.thread.id for item in listed] == [second.id, first.thread.id]
    older = listed[1]
    assert older.title == "What should I eat for dinner?"
    assert older.preview == "Grilled fish with rice fits your budget."
    assert older.preview_role is not None
    assert older.preview_role.value == "assistant"

    # The single-thread routes follow the most recently active thread.
    assert (await repo.get_or_create_thread(user.id)).id == second.id
    await _complete_turn(
        repo,
        user.id,
        content="And lunch?",
        reply="A salad works.",
        key="multi-3",
        thread_id=first.thread.id,
    )
    assert (await repo.get_or_create_thread(user.id)).id == first.thread.id

    page = await repo.list_threads(user_id=user.id, limit=1)
    assert [item.thread.id for item in page] == [first.thread.id]
    page = await repo.list_threads(
        user_id=user.id, limit=1, before_thread_id=first.thread.id
    )
    assert [item.thread.id for item in page] == [second.id]


@pytest.mark.asyncio
async def test_threads_are_private_to_their_owner(async_db_session):
    owner = await _insert_user(async_db_session, "owner")
    other = await _insert_user(async_db_session, "other")
    repo = AsyncChatRepository(async_db_session)
    claim = await _complete_turn(
        repo, owner.id, content="Hi", reply="Hello.", key="private-1"
    )
    thread_id = claim.thread.id

    assert await repo.get_thread(other.id, thread_id) is None
    assert await repo.list_threads(user_id=other.id, limit=10) == []
    with pytest.raises(ChatThreadNotFoundError):
        await repo.claim_turn(
            user_id=other.id,
            content="Hi",
            idempotency_key="private-2",
            request_fingerprint="fp",
            lease_expires_at=utc_now() + timedelta(seconds=90),
            thread_id=thread_id,
        )
    with pytest.raises(ChatThreadNotFoundError):
        await repo.clear_thread(other.id, thread_id)
    reopened = await repo.reopen_assistant_message(
        user_id=other.id,
        assistant_message_id=claim.assistant_message.id,
        lease_expires_at=utc_now() + timedelta(seconds=90),
    )
    assert reopened is None


@pytest.mark.asyncio
async def test_regenerate_is_scoped_to_the_requested_thread(async_db_session):
    user = await _insert_user(async_db_session, "regen")
    repo = AsyncChatRepository(async_db_session)
    claim = await _complete_turn(
        repo, user.id, content="Hi", reply="Hello.", key="regen-1"
    )
    other_thread = await repo.create_thread(user.id)

    wrong_thread = await repo.reopen_assistant_message(
        user_id=user.id,
        assistant_message_id=claim.assistant_message.id,
        lease_expires_at=utc_now() + timedelta(seconds=90),
        thread_id=other_thread.id,
    )
    assert wrong_thread is None
    reopened = await repo.reopen_assistant_message(
        user_id=user.id,
        assistant_message_id=claim.assistant_message.id,
        lease_expires_at=utc_now() + timedelta(seconds=90),
        thread_id=claim.thread.id,
    )
    assert reopened is not None
    assert reopened[0].thread.id == claim.thread.id


@pytest.mark.asyncio
async def test_daily_budget_counts_every_thread(async_db_session):
    user = await _insert_user(async_db_session, "budget")
    repo = AsyncChatRepository(async_db_session)
    since = utc_now() - timedelta(minutes=1)
    await _complete_turn(repo, user.id, content="a", reply="b", key="budget-1")
    second = await repo.create_thread(user.id)
    await _complete_turn(
        repo, user.id, content="c", reply="d", key="budget-2", thread_id=second.id
    )

    assert await repo.count_user_turns_since(user_id=user.id, since=since) == 2


@pytest.mark.asyncio
async def test_clear_one_thread_leaves_the_others(async_db_session):
    user = await _insert_user(async_db_session, "clear")
    repo = AsyncChatRepository(async_db_session)
    first = await _complete_turn(repo, user.id, content="a", reply="b", key="clear-1")
    second = await repo.create_thread(user.id)
    await _complete_turn(
        repo, user.id, content="c", reply="d", key="clear-2", thread_id=second.id
    )

    cleared = await repo.clear_thread(user.id, first.thread.id)

    assert cleared.id == first.thread.id
    listed = await repo.list_threads(user_id=user.id, limit=10)
    assert [item.thread.id for item in listed] == [second.id]


@pytest.mark.asyncio
async def test_account_deletion_removes_every_thread(async_db_session):
    user = await _insert_user(async_db_session, "delete")
    repo = AsyncChatRepository(async_db_session)
    await _complete_turn(repo, user.id, content="a", reply="b", key="delete-1")
    second = await repo.create_thread(user.id)
    await _complete_turn(
        repo, user.id, content="c", reply="d", key="delete-2", thread_id=second.id
    )

    await repo.delete_user_chat(user.id)

    remaining = await async_db_session.execute(
        select(ChatThreadORM.id).where(ChatThreadORM.user_id == user.id)
    )
    assert remaining.all() == []
