"""Live backend Coach turn scenarios. No mobile. Requires OPENAI_API_KEY.

Run:
  uv run pytest tests/integration/chat/test_coach_scenarios_e2e.py -o addopts="" -s -q
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from src.app.services.chat_next_meal_candidates import NextMealCandidateResult
from src.app.services.chat_turn_orchestrator import ChatTurnOrchestrator
from src.domain.model.chat import (
    ChatClaimKind,
    ChatMessage,
    ChatMessageRole,
    ChatMessageStatus,
    ChatThread,
    ChatTurnClaim,
    ChatUserContext,
    RetrievedKnowledgeChunk,
)
from src.domain.services.chat.policy import (
    label_chunks,
    out_of_scope_message,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.adapters.openai_chat_completion_adapter import OpenAIChatCompletionAdapter
from src.infra.adapters.openai_chat_embedding_adapter import OpenAIChatEmbeddingAdapter
from src.infra.config.settings import settings
from src.infra.services.chat_concurrency import reset_chat_concurrency_for_tests


def _has_openai() -> bool:
    return bool(settings.OPENAI_API_KEY or "")


pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(not _has_openai(), reason="OPENAI_API_KEY is required"),
]


@dataclass(frozen=True)
class Scenario:
    id: str
    content: str
    intent: str | None = None
    locale: str = "en"
    expect_intent: str | None = None
    expect_cards: bool = False
    expect_reject: bool = False


SCENARIOS = (
    Scenario(
        "chip_remaining_budget",
        "What's left in my day?",
        intent="remaining_budget",
    ),
    Scenario(
        "chip_day_progress",
        "How's my day going?",
        intent="day_progress",
    ),
    Scenario(
        "chip_next_meal",
        "What should I eat next?",
        intent="next_meal",
        expect_intent="next_meal",
        expect_cards=True,
    ),
    Scenario(
        "chip_limits",
        "What Coach can't do",
        intent="limits",
    ),
    Scenario(
        "typed_remaining_en",
        "How much is left?",
    ),
    Scenario(
        "typed_remaining_vi",
        "Tôi đã ăn bao nhiêu rồi?",
        locale="vi",
    ),
    Scenario(
        "typed_day_progress",
        "Am I on track today?",
    ),
    Scenario(
        "typed_dinner",
        "What's for dinner?",
        expect_intent="next_meal",
        expect_cards=True,
    ),
    Scenario(
        "typed_protein_free_text",
        "What's the function of protein?",
    ),
    Scenario(
        "typed_logged_dinner_no_cards",
        "I already logged dinner",
    ),
    Scenario(
        "typed_coding_en",
        "Write a Python function",
        expect_reject=True,
    ),
    Scenario(
        "typed_coding_vi",
        "Viết hàm Python giúp tôi",
        locale="vi",
        expect_reject=True,
    ),
    Scenario(
        "typed_weather",
        "What's the weather in Hanoi?",
        expect_reject=True,
    ),
)


class _FakeRepo:
    def __init__(self, claim: ChatTurnClaim) -> None:
        self.claim = claim
        self.completed: dict[str, Any] | None = None

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
        return {"protein-guide": ("Protein", None)}

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
        return self.claim.assistant_message

    async def count_user_turns_since(self, **kwargs):
        return 0


class _FakeUow:
    def __init__(self, repo: _FakeRepo) -> None:
        self.session = object()
        self.chat = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _FakeContext:
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


class _FakeRetrieval:
    async def retrieve(self, **kwargs):
        return label_chunks(
            [
                RetrievedKnowledgeChunk(
                    chunk_id="c1",
                    document_id="d1",
                    source_key="protein-guide",
                    title="Protein",
                    content=(
                        "Stay at the Nutree protein target. Protein supports "
                        "satiety and muscle recovery."
                    ),
                    locale="en",
                    canonical_uri=None,
                    label="",
                )
            ]
        )


class _StubNextMeals:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def fetch(self, **kwargs):
        self.calls.append(kwargs)
        return NextMealCandidateResult(
            suggestions=[
                {
                    "id": "d1",
                    "name": "Egg rice bowl",
                    "meal_type": "dinner",
                    "calories": 420,
                    "protein_g": 28,
                    "carbs_g": 45,
                    "fat_g": 12,
                },
                {
                    "id": "d2",
                    "name": "Grilled tofu salad",
                    "meal_type": "dinner",
                    "calories": 380,
                    "protein_g": 24,
                    "carbs_g": 18,
                    "fat_g": 22,
                },
            ],
            meal_slot="dinner",
        )


def _claim() -> ChatTurnClaim:
    now = utc_now()
    thread = ChatThread(id="t-e2e", user_id="u-e2e", created_at=now, updated_at=now)
    user = ChatMessage(
        id=f"m-user-{uuid.uuid4().hex[:8]}",
        thread_id=thread.id,
        role=ChatMessageRole.USER,
        status=ChatMessageStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        content="e2e",
        idempotency_key=str(uuid.uuid4()),
        request_fingerprint="e2e",
    )
    assistant = ChatMessage(
        id=f"m-asst-{uuid.uuid4().hex[:8]}",
        thread_id=thread.id,
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.GENERATING,
        created_at=now,
        updated_at=now,
        in_reply_to_id=user.id,
        model=settings.CHAT_MODEL,
    )
    return ChatTurnClaim(
        kind=ChatClaimKind.NEW,
        thread=thread,
        user_message=user,
        assistant_message=assistant,
    )


def _print_report(rows: list[dict[str, Any]]) -> None:
    print("\n\n======== Coach backend e2e ========")
    for row in rows:
        print(f"\n--- {row['id']} ---")
        print(f"in:      {row['content']!r}  intent={row['sent_intent']!r}  locale={row['locale']}")
        print(f"route:   intent={row['got_intent']!r}  cards={row['card_count']}  reject={row['rejected']}")
        print(f"chips:   {row['follow_ups']}")
        print(f"reply:\n{row['reply']}")
    print("\n======== end e2e ========\n")


@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_coach_scenarios_show_every_response() -> None:
    reset_chat_concurrency_for_tests()
    api_key = settings.OPENAI_API_KEY or ""
    adapter = OpenAIChatCompletionAdapter(
        api_key=api_key,
        timeout_seconds=settings.CHAT_REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
        reasoning_effort=settings.CHAT_REASONING_EFFORT,
    )
    embedding = OpenAIChatEmbeddingAdapter(
        api_key=api_key,
        model=settings.CHAT_EMBEDDING_MODEL,
    )
    next_meals = _StubNextMeals()
    rows: list[dict[str, Any]] = []
    failures: list[str] = []

    try:
        for scenario in SCENARIOS:
            repo = _FakeRepo(_claim())
            orchestrator = ChatTurnOrchestrator(
                completion=adapter,
                embedding=embedding,
                retrieval=_FakeRetrieval(),
                context_builder=_FakeContext(),
                uow_factory=lambda repo=repo: _FakeUow(repo),
                model=settings.CHAT_MODEL,
                daily_turn_budget=40,
                generation_lease_seconds=90,
                global_concurrency=2,
                next_meals=next_meals,
                follow_ups=adapter,
            )
            events = [
                event
                async for event in orchestrator.stream_turn(
                    user_id="u-e2e",
                    content=scenario.content,
                    idempotency_key=str(uuid.uuid4()),
                    locale=scenario.locale,
                    header_timezone="Asia/Ho_Chi_Minh",
                    user_language=scenario.locale,
                    intent=scenario.intent,
                )
            ]
            completed = next(
                event for event in events if event.event == "message.completed"
            )
            reply = (repo.completed or {}).get("content") or ""
            got_intent = completed.data.get("intent")
            cards = completed.data.get("suggestions") or []
            follow_ups = completed.data.get("follow_ups") or []
            rejected = False
            rows.append(
                {
                    "id": scenario.id,
                    "content": scenario.content,
                    "sent_intent": scenario.intent,
                    "locale": scenario.locale,
                    "got_intent": got_intent,
                    "card_count": len(cards),
                    "rejected": rejected,
                    "follow_ups": [
                        f"{item.get('action')}:{item.get('label')}" for item in follow_ups
                    ],
                    "reply": reply,
                }
            )
            if scenario.expect_reject:
                if cards:
                    failures.append(f"{scenario.id}: reject must not attach cards")
                if got_intent:
                    failures.append(f"{scenario.id}: reject must not set intent")
                if "def " in reply or "import " in reply or "sunny" in reply.lower() or "celsius" in reply.lower():
                    failures.append(f"{scenario.id}: reply seems to have actually answered the off-topic prompt: {reply!r}")
                continue
            if not reply.strip():
                failures.append(f"{scenario.id}: empty reply")
            if rejected:
                failures.append(f"{scenario.id}: nutrition question was rejected")
            if scenario.expect_intent and got_intent != scenario.expect_intent:
                failures.append(
                    f"{scenario.id}: intent {got_intent!r} != {scenario.expect_intent!r}"
                )
            if scenario.expect_cards and not cards:
                failures.append(f"{scenario.id}: expected meal cards")
            if not scenario.expect_cards and cards:
                failures.append(f"{scenario.id}: unexpected meal cards")
            if scenario.expect_reject is False and "def " in reply:
                failures.append(f"{scenario.id}: reply looks like code")
    finally:
        _print_report(rows)
        reset_chat_concurrency_for_tests()

    assert not failures, "\n".join(failures)
