"""Unit tests for agent orchestration with typed tools and RAG in ChatTurnOrchestrator."""

from __future__ import annotations

from typing import Any

import pytest

from src.app.services.chat_turn_orchestrator import (
    CHAT_ORCHESTRATOR_TOOLS,
    ChatTurnOrchestrator,
    _merge_retrieved_chunks,
)
from src.domain.model.chat import (
    ChatClaimKind,
    ChatCompletionDelta,
    ChatIntent,
    ChatMessage,
    ChatMessageRole,
    ChatMessageStatus,
    ChatThread,
    ChatTurnClaim,
    ChatUsage,
    ChatUserContext,
    RetrievedKnowledgeChunk,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.services.chat_concurrency import reset_chat_concurrency_for_tests


class _AgentFakeRepo:
    def __init__(self, claim: ChatTurnClaim):
        self.claim = claim
        self.completed: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None

    async def get_or_create_thread(self, user_id: str) -> ChatThread:
        return self.claim.thread

    async def claim_turn(self, **kwargs):
        return self.claim

    async def list_completed_messages(self, **kwargs):
        return []

    async def list_recent_completed_history(self, **kwargs):
        return []

    async def get_generating_turn(self, thread_id: str):
        return None

    async def list_citation_metadata(self, source_keys):
        return {
            "green-tea-guide": ("Green Tea Benefits", "https://example.com/green-tea"),
        }

    async def complete_assistant_message(self, **kwargs):
        self.completed = kwargs
        message = self.claim.assistant_message
        return ChatMessage(
            id=message.id,
            thread_id=message.thread_id,
            role=message.role,
            status=ChatMessageStatus.COMPLETED,
            created_at=message.created_at,
            updated_at=utc_now(),
            content=kwargs["content"],
            model=kwargs["model"],
            citation_source_keys=kwargs["citation_source_keys"],
            input_tokens=kwargs["usage"].input_tokens,
            output_tokens=kwargs["usage"].output_tokens,
            cached_tokens=kwargs["usage"].cached_tokens,
            completed_at=utc_now(),
            reply_payload=kwargs.get("reply_payload"),
        )

    async def fail_assistant_message(self, **kwargs):
        self.failed = kwargs
        return self.claim.assistant_message

    async def count_user_turns_since(self, **kwargs):
        return 0


class _AgentFakeUow:
    def __init__(self, repo: _AgentFakeRepo):
        self.chat = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _AgentMultiTurnCompletion:
    def __init__(self, turns: list[list[ChatCompletionDelta]]):
        self._turns = turns
        self.call_count = 0
        self.received_histories: list[list[Any]] = []
        self.received_tools: list[Any] = []

    async def stream(self, **kwargs):
        self.received_histories.append(list(kwargs.get("history") or []))
        self.received_tools.append(kwargs.get("tools"))
        if self.call_count < len(self._turns):
            deltas = self._turns[self.call_count]
        else:
            deltas = [ChatCompletionDelta(text="Default fallback text. ", done=True)]
        self.call_count += 1
        for delta in deltas:
            yield delta


class _AgentFakeContext:
    async def build(self, **kwargs) -> ChatUserContext:
        return ChatUserContext(
            context_version="chat_context_v1",
            as_of="2026-09-01T00:00:00+00:00",
            locale=kwargs.get("locale") or "vi",
            timezone="Asia/Ho_Chi_Minh",
            allergies=["shrimp"],
            health_conditions=[],
            dietary_preferences=["high_protein"],
            goal="fat_loss",
            tdee=2100,
            target_calories=1800,
            target_protein_g=140,
            target_carbs_g=180,
            target_fat_g=55,
            food_calories=800,
            movement_kcal_burned=0,
            local_date="2026-09-01",
            consumed_calories=800,
            consumed_protein_g=60,
            consumed_carbs_g=90,
            consumed_fat_g=25,
            remaining_calories=1000,
            remaining_protein_g=80,
            remaining_carbs_g=90,
            remaining_fat_g=30,
            remaining_days=5,
            local_hour=12,
            local_minute=15,
            suggested_meal_slot="lunch",
        )


class _AgentFakeEmbedding:
    async def embed_query(self, text: str) -> list[float]:
        return [0.05, 0.15]


class _AgentFakeRetrieval:
    def __init__(self, chunks: list[RetrievedKnowledgeChunk] | None = None):
        self.chunks = chunks or []

    async def retrieve(self, **kwargs):
        return self.chunks


def _make_agent_claim():
    now = utc_now()
    thread = ChatThread(id="t-agent", user_id="u-agent", created_at=now, updated_at=now)
    user = ChatMessage(
        id="m-agent-user",
        thread_id="t-agent",
        role=ChatMessageRole.USER,
        status=ChatMessageStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        content="Hello coach",
        idempotency_key="key-agent",
        request_fingerprint="fp-agent",
    )
    assistant = ChatMessage(
        id="m-agent-asst",
        thread_id="t-agent",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.GENERATING,
        created_at=now,
        updated_at=now,
        in_reply_to_id="m-agent-user",
        model="gpt-5.6-luna",
    )
    return ChatTurnClaim(
        kind=ChatClaimKind.NEW,
        thread=thread,
        user_message=user,
        assistant_message=assistant,
    )


def _orchestrator(repo, completion, retrieval=None, next_meals=None, follow_ups=None):
    return ChatTurnOrchestrator(
        completion=completion,
        embedding=_AgentFakeEmbedding(),
        retrieval=retrieval or _AgentFakeRetrieval(),
        context_builder=_AgentFakeContext(),
        uow_factory=lambda: _AgentFakeUow(repo),
        model="gpt-5.6-luna",
        daily_turn_budget=40,
        generation_lease_seconds=90,
        global_concurrency=2,
        next_meals=next_meals,
        follow_ups=follow_ups,
    )


@pytest.fixture(autouse=True)
def _reset_concurrency():
    reset_chat_concurrency_for_tests()
    yield
    reset_chat_concurrency_for_tests()


def test_orchestrator_tool_schemas_are_valid():
    """Verify orchestrator tools include suggest_next_meal for LLM-driven intent detection."""
    tool_names = [t["function"]["name"] for t in CHAT_ORCHESTRATOR_TOOLS]
    assert "suggest_next_meal" in tool_names
    assert "check_daily_progress" in tool_names
    assert "search_nutrition_knowledge" in tool_names
    assert "explain_limits_and_guidelines" in tool_names
    for t in CHAT_ORCHESTRATOR_TOOLS:
        assert t["type"] == "function"
        fn = t["function"]
        assert len(fn["description"]) > 10
        assert "parameters" in fn


def test_merge_retrieved_chunks_assigns_sequential_labels():
    """Verify new chunks are given next sequential [Kn] labels without duplicates."""
    c1 = RetrievedKnowledgeChunk(
        chunk_id="c1",
        document_id="d1",
        source_key="s1",
        title="T1",
        content="Content 1",
        locale="vi",
        canonical_uri=None,
        label="[K1]",
    )
    c2 = RetrievedKnowledgeChunk(
        chunk_id="c2",
        document_id="d2",
        source_key="s2",
        title="T2",
        content="Content 2",
        locale="vi",
        canonical_uri=None,
        label="[K2]",
    )
    c3 = RetrievedKnowledgeChunk(
        chunk_id="c3",
        document_id="d3",
        source_key="s3",
        title="T3",
        content="Content 3",
        locale="vi",
        canonical_uri=None,
        label="[K1]",
    )
    # Merging c1 and duplicate c1 + new c3
    merged = _merge_retrieved_chunks([c1, c2], [c1, c3])
    assert len(merged) == 3
    assert merged[0].label == "[K1]"
    assert merged[1].label == "[K2]"
    assert merged[2].label == "[K3]"
    assert merged[2].source_key == "s3"


@pytest.mark.asyncio
async def test_agent_tool_explain_limits_and_guidelines():
    """Verify explain_limits_and_guidelines sets intent=limits in payload."""
    repo = _AgentFakeRepo(_make_agent_claim())
    turn_1 = [
        ChatCompletionDelta(
            text="",
            tool_calls=[
                {"id": "c-limits", "name": "explain_limits_and_guidelines", "args": {}}
            ],
            done=True,
        )
    ]
    turn_2 = [
        ChatCompletionDelta(text="Nutree có thể hỗ trợ tính calo và gợi ý bữa ăn. "),
        ChatCompletionDelta(
            text="",
            usage=ChatUsage(input_tokens=25, output_tokens=20, model="gpt-5.6-luna"),
            done=True,
        ),
    ]
    completion = _AgentMultiTurnCompletion([turn_1, turn_2])
    orchestrator = _orchestrator(repo, completion)

    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u-agent",
            content="Nutree có thể giúp tôi những gì?",
            idempotency_key="key-limits",
            locale="vi",
            header_timezone="Asia/Ho_Chi_Minh",
            user_language="vi",
        )
    ]

    completed = next(e for e in events if e.event == "message.completed")
    assert completed.data["intent"] == ChatIntent.LIMITS.value
    assert "Nutree có thể hỗ trợ" in repo.completed["content"]
    # Check tool result was fed to turn 2 history
    tool_turns = [t for t in completion.received_histories[1] if t.role == "tool"]
    assert len(tool_turns) == 1
    assert tool_turns[0].name == "explain_limits_and_guidelines"
    assert "Nutree Coach capabilities" in tool_turns[0].content


@pytest.mark.asyncio
async def test_agent_tool_check_daily_progress_day_progress_focus():
    """Verify check_daily_progress with focus=day_progress sets intent=day_progress."""
    repo = _AgentFakeRepo(_make_agent_claim())
    turn_1 = [
        ChatCompletionDelta(
            text="",
            tool_calls=[
                {
                    "id": "c-prog",
                    "name": "check_daily_progress",
                    "args": {"focus": "day_progress"},
                }
            ],
            done=True,
        )
    ]
    turn_2 = [
        ChatCompletionDelta(text="Tiến độ hôm nay của bạn rất tốt. "),
        ChatCompletionDelta(
            text="",
            usage=ChatUsage(input_tokens=30, output_tokens=15, model="gpt-5.6-luna"),
            done=True,
        ),
    ]
    completion = _AgentMultiTurnCompletion([turn_1, turn_2])
    orchestrator = _orchestrator(repo, completion)

    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u-agent",
            content="Tiến độ dinh dưỡng hôm nay của tôi thế nào?",
            idempotency_key="key-prog",
            locale="vi",
            header_timezone="Asia/Ho_Chi_Minh",
            user_language="vi",
        )
    ]

    completed = next(e for e in events if e.event == "message.completed")
    assert completed.data["intent"] == ChatIntent.DAY_PROGRESS.value
    assert repo.completed["reply_payload"]["intent"] == ChatIntent.DAY_PROGRESS.value


@pytest.mark.asyncio
async def test_agent_tool_search_nutrition_knowledge_with_citations():
    """Verify search_nutrition_knowledge retrieves chunks and passes citation validation."""
    repo = _AgentFakeRepo(_make_agent_claim())
    knowledge_chunk = RetrievedKnowledgeChunk(
        chunk_id="chunk-tea",
        document_id="doc-tea",
        source_key="green-tea-guide",
        title="Lợi ích của trà xanh",
        content="Trà xanh giàu chất chống oxy hóa EGCG hỗ trợ trao đổi chất.",
        locale="vi",
        canonical_uri="https://example.com/green-tea",
        label="[K1]",
        fused_score=0.95,
    )
    retrieval = _AgentFakeRetrieval([knowledge_chunk])

    turn_1 = [
        ChatCompletionDelta(
            text="",
            tool_calls=[
                {
                    "id": "c-rag",
                    "name": "search_nutrition_knowledge",
                    "args": {"query": "trà xanh"},
                }
            ],
            done=True,
        )
    ]
    turn_2 = [
        ChatCompletionDelta(text="Theo [K1], trà xanh giàu chất chống oxy hóa EGCG. "),
        ChatCompletionDelta(
            text="",
            usage=ChatUsage(input_tokens=40, output_tokens=25, model="gpt-5.6-luna"),
            done=True,
        ),
    ]
    completion = _AgentMultiTurnCompletion([turn_1, turn_2])
    orchestrator = _orchestrator(repo, completion, retrieval=retrieval)

    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u-agent",
            content="Uống trà xanh có tác dụng gì?",
            idempotency_key="key-rag",
            locale="vi",
            header_timezone="Asia/Ho_Chi_Minh",
            user_language="vi",
        )
    ]

    completed = next(e for e in events if e.event == "message.completed")
    assert len(completed.data["citations"]) >= 1
    assert completed.data["citations"][0]["label"] == "[K1]"
    assert completed.data["citations"][0]["source_key"] == "green-tea-guide"
    assert "Theo [K1]" in repo.completed["content"]


@pytest.mark.asyncio
async def test_agent_direct_synthesis_without_tools():
    """Verify general conversation without tools streams directly in 1 turn."""
    repo = _AgentFakeRepo(_make_agent_claim())
    turn_1 = [
        ChatCompletionDelta(
            text="Chào bạn! Tôi là Nutree Coach, sẵn sàng đồng hành cùng bạn. "
        ),
        ChatCompletionDelta(
            text="",
            usage=ChatUsage(input_tokens=15, output_tokens=18, model="gpt-5.6-luna"),
            done=True,
        ),
    ]
    completion = _AgentMultiTurnCompletion([turn_1])
    orchestrator = _orchestrator(repo, completion)

    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u-agent",
            content="Xin chào!",
            idempotency_key="key-hello",
            locale="vi",
            header_timezone="Asia/Ho_Chi_Minh",
            user_language="vi",
        )
    ]

    assert completion.call_count == 1
    completed = next(e for e in events if e.event == "message.completed")
    assert (
        "Chào bạn!" in completed.data.get("reply_payload", {}).get("suggestions", [])
        or "Chào bạn!" in repo.completed["content"]
    )
