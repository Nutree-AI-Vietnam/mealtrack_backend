"""Edge-case tests for LLM Tool Calling in ChatTurnOrchestrator."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.app.services.chat_next_meal_candidates import NextMealCandidateResult
from src.app.services.chat_turn_orchestrator import ChatTurnOrchestrator
from src.domain.model.chat import (
    ChatClaimKind,
    ChatCompletionDelta,
    ChatMessage,
    ChatMessageRole,
    ChatMessageStatus,
    ChatThread,
    ChatTurnClaim,
    ChatUsage,
    ChatUserContext,
)
from src.domain.services.chat.policy import safe_fallback_message
from src.domain.utils.timezone_utils import utc_now
from src.infra.services.chat_concurrency import reset_chat_concurrency_for_tests


class _EdgeFakeRepo:
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
        return {}

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


class _EdgeFakeUow:
    def __init__(self, repo: _EdgeFakeRepo):
        self.chat = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _EdgeMultiTurnCompletion:
    """Simulates multi-step tool calls and responses."""

    def __init__(self, turns: list[list[ChatCompletionDelta]]):
        self._turns = turns
        self.call_count = 0
        self.received_histories: list[list[Any]] = []

    async def stream(self, **kwargs):
        self.received_histories.append(list(kwargs.get("history") or []))
        if self.call_count < len(self._turns):
            deltas = self._turns[self.call_count]
        else:
            deltas = [
                ChatCompletionDelta(text="Default fallback text. ", done=True)
            ]
        self.call_count += 1
        for delta in deltas:
            yield delta


class _EdgeFakeContext:
    async def build(self, **kwargs) -> ChatUserContext:
        return ChatUserContext(
            context_version="chat_context_v1",
            as_of="2026-09-01T00:00:00+00:00",
            locale=kwargs.get("locale") or "en",
            timezone="UTC",
            allergies=["peanut"],
            health_conditions=[],
            dietary_preferences=[],
            goal="cutting",
            tdee=2200,
            target_calories=1800,
            target_protein_g=140,
            target_carbs_g=180,
            target_fat_g=60,
            consumed_calories=1150,
            consumed_protein_g=90,
            consumed_carbs_g=100,
            consumed_fat_g=40,
            remaining_calories=650,
            remaining_protein_g=50,
            remaining_carbs_g=80,
            remaining_fat_g=20,
            remaining_days=4,
            local_hour=18,
            local_minute=30,
            suggested_meal_slot="dinner",
        )


class _EdgeFakeEmbedding:
    async def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2]


class _EdgeFakeRetrieval:
    async def retrieve(self, **kwargs):
        return []


def _make_claim():
    now = utc_now()
    thread = ChatThread(id="t-edge", user_id="u-edge", created_at=now, updated_at=now)
    user = ChatMessage(
        id="m-edge-user",
        thread_id="t-edge",
        role=ChatMessageRole.USER,
        status=ChatMessageStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        content="What's for dinner?",
        idempotency_key="key-edge",
        request_fingerprint="fp-edge",
    )
    assistant = ChatMessage(
        id="m-edge-asst",
        thread_id="t-edge",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.GENERATING,
        created_at=now,
        updated_at=now,
        in_reply_to_id="m-edge-user",
        model="gpt-5.6-luna",
    )
    return ChatTurnClaim(
        kind=ChatClaimKind.NEW,
        thread=thread,
        user_message=user,
        assistant_message=assistant,
    )


def _orchestrator(repo, completion, next_meals=None, follow_ups=None):
    return ChatTurnOrchestrator(
        completion=completion,
        embedding=_EdgeFakeEmbedding(),
        retrieval=_EdgeFakeRetrieval(),
        context_builder=_EdgeFakeContext(),
        uow_factory=lambda: _EdgeFakeUow(repo),
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


@pytest.mark.asyncio
async def test_tool_call_with_pre_tool_text_concatenates_properly():
    """Edge Case 1: Model streams text before the tool call, then streams answer after tool call."""
    repo = _EdgeFakeRepo(_make_claim())
    turn_1 = [
        ChatCompletionDelta(text="Checking dinner options for you... "),
        ChatCompletionDelta(text="", 
            tool_calls=[{"id": "c1", "name": "suggest_next_meal"}],
            done=True,
        ),
    ]
    turn_2 = [
        ChatCompletionDelta(text="Here is an egg rice bowl option. "),
        ChatCompletionDelta(
            text="",
            usage=ChatUsage(input_tokens=20, output_tokens=15, model="gpt-5.6-luna"),
            done=True,
        ),
    ]
    completion = _EdgeMultiTurnCompletion([turn_1, turn_2])

    class _MockMeals:
        async def fetch(self, **kwargs):
            return NextMealCandidateResult(
                suggestions=[{"id": "m1", "name": "Egg rice bowl", "calories": 420, "protein_g": 28}],
                meal_slot="dinner",
            )

    orchestrator = _orchestrator(repo, completion, next_meals=_MockMeals())
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u-edge",
            content="What's for dinner?",
            idempotency_key="key-edge",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        )
    ]
    completed = next(e for e in events if e.event == "message.completed")
    assert repo.completed is not None
    full_content = repo.completed["content"]
    assert "Checking dinner options for you..." in full_content
    assert "Here is an egg rice bowl option." in full_content
    assert completed.data["intent"] == "next_meal"
    assert len(completed.data["suggestions"]) == 1

    turn_2_history = completion.received_histories[1]
    assert turn_2_history[0].role == ChatMessageRole.USER
    assert turn_2_history[0].content == "What's for dinner?"
    assert turn_2_history[1].role == ChatMessageRole.ASSISTANT
    assert turn_2_history[1].tool_calls == [{"id": "c1", "name": "suggest_next_meal"}]
    assert turn_2_history[2].role == "tool"
    assert "Egg rice bowl" in turn_2_history[2].content


@pytest.mark.asyncio
async def test_tool_call_handles_next_meals_exception_gracefully():
    """Edge Case 2: Next-meal service raises an exception; turn must not crash."""
    repo = _EdgeFakeRepo(_make_claim())
    turn_1 = [
        ChatCompletionDelta(text="", 
            tool_calls=[{"id": "c2", "name": "suggest_next_meal"}],
            done=True,
        ),
    ]
    turn_2 = [
        ChatCompletionDelta(text="Catalog is temporarily down, but focus on lean protein. "),
        ChatCompletionDelta(text="", done=True),
    ]
    completion = _EdgeMultiTurnCompletion([turn_1, turn_2])

    class _FailingMeals:
        async def fetch(self, **kwargs):
            raise TimeoutError("Database query timed out")

    orchestrator = _orchestrator(repo, completion, next_meals=_FailingMeals())
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u-edge",
            content="What's for dinner?",
            idempotency_key="key-edge",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        )
    ]
    completed = next(e for e in events if e.event == "message.completed")
    assert completed.data["suggestions"] == []
    assert "Catalog is temporarily down" in repo.completed["content"]
    turn_2_history = completion.received_histories[1]
    assert "unavailable" in turn_2_history[2].content.lower()


@pytest.mark.asyncio
async def test_tool_call_handles_empty_meal_candidates():
    """Edge Case 3: Next meals returns empty list due to strict constraints."""
    repo = _EdgeFakeRepo(_make_claim())
    turn_1 = [
        ChatCompletionDelta(text="", 
            tool_calls=[{"id": "c3", "name": "suggest_next_meal"}],
            done=True,
        ),
    ]
    turn_2 = [
        ChatCompletionDelta(text="No matching recipes found, but you can have grilled chicken. "),
        ChatCompletionDelta(text="", done=True),
    ]
    completion = _EdgeMultiTurnCompletion([turn_1, turn_2])

    class _EmptyMeals:
        async def fetch(self, **kwargs):
            return NextMealCandidateResult(suggestions=[], meal_slot="dinner")

    orchestrator = _orchestrator(repo, completion, next_meals=_EmptyMeals())
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u-edge",
            content="What's for dinner?",
            idempotency_key="key-edge",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        )
    ]
    completed = next(e for e in events if e.event == "message.completed")
    assert completed.data["suggestions"] == []
    turn_2_history = completion.received_histories[1]
    assert "no meal options found" in turn_2_history[2].content.lower()


@pytest.mark.asyncio
async def test_tool_call_handles_unknown_tool_name():
    """Edge Case 4: Model hallucinated an unsupported tool name."""
    repo = _EdgeFakeRepo(_make_claim())
    turn_1 = [
        ChatCompletionDelta(text="", 
            tool_calls=[{"id": "c4", "name": "web_search_recipes"}],
            done=True,
        ),
    ]
    turn_2 = [
        ChatCompletionDelta(text="I cannot browse the web, but here is standard nutrition info. "),
        ChatCompletionDelta(text="", done=True),
    ]
    completion = _EdgeMultiTurnCompletion([turn_1, turn_2])

    orchestrator = _orchestrator(repo, completion)
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u-edge",
            content="Find recipes online",
            idempotency_key="key-edge",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        )
    ]
    completed = next(e for e in events if e.event == "message.completed")
    turn_2_history = completion.received_histories[1]
    assert "not recognized" in turn_2_history[2].content.lower()


@pytest.mark.asyncio
async def test_tool_call_handles_multiple_tools_in_single_turn():
    """Edge Case 5: Model emits both check_daily_progress and suggest_next_meal."""
    repo = _EdgeFakeRepo(_make_claim())
    turn_1 = [
        ChatCompletionDelta(text="", 
            tool_calls=[
                {"id": "call_p", "name": "check_daily_progress"},
                {"id": "call_m", "name": "suggest_next_meal"},
            ],
            done=True,
        ),
    ]
    turn_2 = [
        ChatCompletionDelta(text="You have 650 kcal left. Here is dinner. "),
        ChatCompletionDelta(text="", done=True),
    ]
    completion = _EdgeMultiTurnCompletion([turn_1, turn_2])

    class _MockMeals:
        async def fetch(self, **kwargs):
            return NextMealCandidateResult(
                suggestions=[{"id": "m1", "name": "Tofu salad", "calories": 350, "protein_g": 20}],
                meal_slot="dinner",
            )

    orchestrator = _orchestrator(repo, completion, next_meals=_MockMeals())
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u-edge",
            content="Check my day and dinner",
            idempotency_key="key-edge",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        )
    ]
    completed = next(e for e in events if e.event == "message.completed")
    assert len(completed.data["suggestions"]) == 1
    turn_2_history = completion.received_histories[1]
    tool_turns = [t for t in turn_2_history if t.role == "tool"]
    assert len(tool_turns) == 2
    assert any("Daily progress" in t.content for t in tool_turns)
    assert any("Found 1 meal options" in t.content for t in tool_turns)


@pytest.mark.asyncio
async def test_tool_call_handles_missing_call_id():
    """Edge Case 6: Model emits tool call with missing/None id."""
    repo = _EdgeFakeRepo(_make_claim())
    turn_1 = [
        ChatCompletionDelta(text="", 
            tool_calls=[{"name": "check_daily_progress", "id": None}],
            done=True,
        ),
    ]
    turn_2 = [
        ChatCompletionDelta(text="You have 650 calories remaining today. "),
        ChatCompletionDelta(text="", done=True),
    ]
    completion = _EdgeMultiTurnCompletion([turn_1, turn_2])

    orchestrator = _orchestrator(repo, completion)
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u-edge",
            content="Check progress",
            idempotency_key="key-edge",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        )
    ]
    completed = next(e for e in events if e.event == "message.completed")
    turn_2_history = completion.received_histories[1]
    tool_turn = next(t for t in turn_2_history if t.role == "tool")
    assert tool_turn.tool_call_id is not None
    assert tool_turn.tool_call_id.startswith("call_")


@pytest.mark.asyncio
async def test_tool_call_response_blocked_on_allergen_leak():
    """Edge Case 7: Assistant tries to recommend peanut despite user peanut allergy."""
    repo = _EdgeFakeRepo(_make_claim())
    turn_1 = [
        ChatCompletionDelta(text="", 
            tool_calls=[{"id": "c7", "name": "suggest_next_meal"}],
            done=True,
        ),
    ]
    turn_2 = [
        ChatCompletionDelta(text="You should eat peanut butter toast. "),
        ChatCompletionDelta(text="", done=True),
    ]
    completion = _EdgeMultiTurnCompletion([turn_1, turn_2])

    class _MockMeals:
        async def fetch(self, **kwargs):
            return NextMealCandidateResult(
                suggestions=[{"id": "m1", "name": "Peanut Toast", "calories": 400, "protein_g": 15}],
                meal_slot="dinner",
            )

    orchestrator = _orchestrator(repo, completion, next_meals=_MockMeals())
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u-edge",
            content="Give me a high protein snack",
            idempotency_key="key-edge",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        )
    ]
    completed = next(e for e in events if e.event == "message.completed")
    assert repo.completed["content"] == safe_fallback_message("en")
