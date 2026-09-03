import asyncio

import pytest

from src.app.services.chat_next_meal_candidates import NextMealCandidateResult
from src.app.services.chat_turn_orchestrator import ChatTurnOrchestrator
from src.domain.exceptions.chat_exceptions import (
    ChatBusyError,
    ChatProviderUnavailableError,
    ChatRateLimitedError,
)
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
    empty_reply_payload,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.services.chat_concurrency import reset_chat_concurrency_for_tests


class _FakeRepo:
    def __init__(self, claim=None, history=None, turns_used=0):
        self.claim = claim
        self.history = history or []
        self.turns_used = turns_used
        self.completed = None
        self.failed = None
        self.cleared = False

    async def get_or_create_thread(self, user_id: str) -> ChatThread:
        return self.claim.thread

    async def claim_turn(self, **kwargs):
        if isinstance(self.claim, Exception):
            raise self.claim
        return self.claim

    async def list_completed_messages(self, **kwargs):
        return list(reversed(self.history))

    async def list_recent_completed_history(self, **kwargs):
        return self.history

    async def get_generating_turn(self, thread_id: str):
        return getattr(self, "generating_turn", None)

    async def list_citation_metadata(self, source_keys):
        return getattr(self, "citation_metadata", {})

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
        return self.turns_used

    async def clear_thread(self, user_id: str):
        self.cleared = True
        return self.claim.thread


class _FakeUow:
    def __init__(self, repo: _FakeRepo):
        self.session = object()
        self.chat = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _FakeCompletion:
    def __init__(self, chunks: list[str]):
        self.chunks = chunks

    async def stream(self, **kwargs):
        for chunk in self.chunks:
            yield ChatCompletionDelta(text=chunk)
        yield ChatCompletionDelta(
            text="",
            usage=ChatUsage(input_tokens=10, output_tokens=4, model="gpt-5.6-luna"),
            done=True,
        )


class _GatedCompletion:
    """Yields the first token, then waits until the test releases the rest."""

    def __init__(self, first: str, second: str):
        self.first = first
        self.second = second
        self.release = asyncio.Event()

    async def stream(self, **kwargs):
        yield ChatCompletionDelta(text=self.first)
        await self.release.wait()
        yield ChatCompletionDelta(text=self.second)
        yield ChatCompletionDelta(
            text="",
            usage=ChatUsage(input_tokens=10, output_tokens=4, model="gpt-5.6-luna"),
            done=True,
        )


class _FakeEmbedding:
    async def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2]


class _FakeRetrieval:
    async def retrieve(self, **kwargs):
        return []


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
            local_hour=8,
            local_minute=12,
            suggested_meal_slot="breakfast",
        )


class _OpenCircuit:
    def get_state(self, model: str) -> str:
        return "open"

    def record_success(self, model: str) -> None:
        return None

    def record_failure(self, model: str) -> None:
        return None


def _claim(
    kind=ChatClaimKind.NEW,
    assistant_content=None,
    status=ChatMessageStatus.GENERATING,
    citation_source_keys=(),
):
    now = utc_now()
    thread = ChatThread(id="t1", user_id="u1", created_at=now, updated_at=now)
    user = ChatMessage(
        id="m-user",
        thread_id="t1",
        role=ChatMessageRole.USER,
        status=ChatMessageStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        content="How much is left?",
        idempotency_key="key-1",
        request_fingerprint="abc",
    )
    assistant = ChatMessage(
        id="m-asst",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=status,
        created_at=now,
        updated_at=now,
        content=assistant_content,
        in_reply_to_id="m-user",
        model="gpt-5.6-luna",
        citation_source_keys=citation_source_keys,
    )
    return ChatTurnClaim(
        kind=kind, thread=thread, user_message=user, assistant_message=assistant
    )


@pytest.fixture(autouse=True)
def _reset_concurrency():
    reset_chat_concurrency_for_tests()
    yield
    reset_chat_concurrency_for_tests()


class _FakeNextMeals:
    def __init__(self, result: NextMealCandidateResult):
        self.result = result
        self.calls: list[dict] = []

    async def fetch(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class _FakeFollowUps:
    def __init__(self, chips=None, error=None):
        self.chips = chips or []
        self.error = error
        self.calls: list[dict] = []

    async def generate_follow_ups(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.chips


def _orchestrator(
    repo,
    completion=None,
    turns_used=0,
    circuit_breaker=None,
    next_meals=None,
    follow_ups=None,
):
    repo.turns_used = turns_used
    uow = _FakeUow(repo)

    return ChatTurnOrchestrator(
        completion=completion
        or _FakeCompletion(["Nutree has 650 calories remaining. "]),
        embedding=_FakeEmbedding(),
        retrieval=_FakeRetrieval(),
        context_builder=_FakeContext(),
        uow_factory=lambda: uow,
        model="gpt-5.6-luna",
        daily_turn_budget=40,
        generation_lease_seconds=90,
        global_concurrency=2,
        circuit_breaker=circuit_breaker,
        next_meals=next_meals,
        follow_ups=follow_ups,
    )


@pytest.mark.asyncio
async def test_replay_emits_started_delta_and_completed():
    claim = _claim(
        kind=ChatClaimKind.REPLAY,
        assistant_content="Nutree has 650 remaining.",
        status=ChatMessageStatus.COMPLETED,
    )
    repo = _FakeRepo(claim=claim)
    orchestrator = _orchestrator(repo)
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="How much is left?",
            idempotency_key="key-1",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        )
    ]
    assert [event.event for event in events] == [
        "message.started",
        "message.delta",
        "message.completed",
    ]
    assert events[2].data["replayed"] is True
    assert events[2].data["suggestions"] == []
    assert events[2].data["follow_ups"] == []


@pytest.mark.asyncio
async def test_new_turn_streams_sentence_and_persists():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo)
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="How much is left?",
            idempotency_key="key-1",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        )
    ]
    names = [event.event for event in events]
    assert names[0] == "message.started"
    assert "message.delta" in names
    assert names[-1] == "message.completed"
    assert repo.completed is not None
    assert "650" in repo.completed["content"]
    assert repo.completed["reply_payload"] == empty_reply_payload()
    completed = next(event for event in events if event.event == "message.completed")
    assert completed.data["suggestions"] == []
    assert completed.data["follow_ups"] == []


@pytest.mark.asyncio
async def test_deltas_are_streamed_before_generation_finishes():
    completion = _GatedCompletion(
        first="Nutree has ",
        second="650 calories remaining. ",
    )
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, completion=completion)
    events: list = []

    async def consume() -> None:
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="How much is left?",
            idempotency_key="key-1",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        ):
            events.append(event)
            if event.event == "message.delta" and "Nutree has " in event.data.get(
                "delta", ""
            ):
                completion.release.set()

    await asyncio.wait_for(consume(), timeout=2)
    deltas = [
        event.data.get("delta", "")
        for event in events
        if event.event == "message.delta"
    ]
    assert deltas[0] == "Nutree has "
    assert "650 calories remaining. " in deltas
    assert events[-1].event == "message.completed"


@pytest.mark.asyncio
async def test_invalid_citation_is_not_streamed_to_client():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(
        repo,
        completion=_FakeCompletion(
            ["According to Nutree [K9], stay at your protein target. "]
        ),
    )
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="Cite protein guidance",
            idempotency_key="key-1",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        )
    ]
    deltas = "".join(
        event.data.get("delta", "")
        for event in events
        if event.event == "message.delta"
    )
    assert "[K9]" not in deltas
    assert repo.completed is not None
    assert "[K9]" not in repo.completed["content"]
    assert "reviewed Nutree guidance" in repo.completed["content"]


@pytest.mark.asyncio
async def test_untraceable_nutrition_is_not_streamed_to_client():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(
        repo,
        completion=_FakeCompletion(["Eat 9999 calories of cake. "]),
    )
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="What should I eat?",
            idempotency_key="key-1",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        )
    ]
    deltas = "".join(
        event.data.get("delta", "")
        for event in events
        if event.event == "message.delta"
    )
    assert "9999" not in deltas
    assert repo.completed is not None
    assert "9999" not in repo.completed["content"]


@pytest.mark.asyncio
async def test_daily_budget_raises_before_generation():
    repo = _FakeRepo(claim=_claim(), turns_used=40)
    orchestrator = _orchestrator(repo, turns_used=40)
    with pytest.raises(ChatRateLimitedError):
        await orchestrator.prepare_turn(
            user_id="u1",
            content="Hi",
            idempotency_key="key-1",
            locale="en",
            header_timezone=None,
            user_language="en",
        )


@pytest.mark.asyncio
async def test_busy_error_surfaces_from_prepare():
    repo = _FakeRepo(claim=ChatBusyError())
    orchestrator = _orchestrator(repo)
    with pytest.raises(ChatBusyError):
        await orchestrator.prepare_turn(
            user_id="u1",
            content="Hi",
            idempotency_key="key-1",
            locale="en",
            header_timezone=None,
            user_language="en",
        )


@pytest.mark.asyncio
async def test_open_circuit_fails_claimed_turn_before_stream():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, circuit_breaker=_OpenCircuit())
    with pytest.raises(ChatProviderUnavailableError):
        await orchestrator.prepare_turn(
            user_id="u1",
            content="Hi",
            idempotency_key="key-1",
            locale="en",
            header_timezone=None,
            user_language="en",
        )
    assert repo.failed is not None
    assert repo.failed["error_code"] == "CHAT_PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_replay_hydrates_citation_titles():
    claim = _claim(
        kind=ChatClaimKind.REPLAY,
        assistant_content="Stay at your protein target [K1].",
        status=ChatMessageStatus.COMPLETED,
        citation_source_keys=("protein-guide",),
    )
    repo = _FakeRepo(claim=claim)
    repo.citation_metadata = {
        "protein-guide": ("Protein", "https://nutree.app/protein"),
    }
    orchestrator = _orchestrator(repo)
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="How much is left?",
            idempotency_key="key-1",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        )
    ]
    completed = next(event for event in events if event.event == "message.completed")
    assert completed.data["citations"] == [
        {
            "label": "[K1]",
            "source_key": "protein-guide",
            "title": "Protein",
            "canonical_uri": "https://nutree.app/protein",
        }
    ]


@pytest.mark.asyncio
async def test_get_thread_includes_in_flight_and_citations():
    claim = _claim(
        status=ChatMessageStatus.GENERATING,
        citation_source_keys=("protein-guide",),
    )
    completed = ChatMessage(
        id="m-done",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.COMPLETED,
        created_at=utc_now(),
        updated_at=utc_now(),
        content="Stay at protein [K1].",
        citation_source_keys=("protein-guide",),
        model="gpt-5.6-luna",
    )
    repo = _FakeRepo(claim=claim, history=[completed])
    repo.generating_turn = (claim.user_message, claim.assistant_message)
    repo.citation_metadata = {"protein-guide": ("Protein", None)}
    orchestrator = _orchestrator(repo)

    payload = await orchestrator.get_thread(user_id="u1", limit=50, before=None)

    assert payload["in_flight"]["assistant_message_id"] == "m-asst"
    assert payload["in_flight"]["idempotency_key"] == "key-1"
    assert payload["messages"][0]["citations"][0]["title"] == "Protein"
    assert payload["messages"][0]["citations"][0]["label"] == "[K1]"
    assert payload["messages"][0]["suggestions"] == []
    assert payload["messages"][0]["follow_ups"] == []


def _three_cards() -> list[dict]:
    return [
        {
            "id": "d1",
            "name": "Egg rice bowl",
            "meal_type": "breakfast",
            "calories": 420,
            "protein_g": 28,
            "carbs_g": 45,
            "fat_g": 12,
        },
        {
            "id": "d2",
            "name": "Yogurt cup",
            "meal_type": "breakfast",
            "calories": 220,
            "protein_g": 18,
            "carbs_g": 20,
            "fat_g": 6,
        },
        {
            "id": "d3",
            "name": "Tofu scramble",
            "meal_type": "breakfast",
            "calories": 380,
            "protein_g": 24,
            "carbs_g": 18,
            "fat_g": 22,
        },
    ]


async def _stream(orchestrator, *, intent=None, content="What should I eat next?"):
    return [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content=content,
            idempotency_key="key-1",
            locale="en",
            header_timezone="UTC",
            user_language="en",
            intent=intent,
        )
    ]


@pytest.mark.asyncio
async def test_next_meal_persists_suggestions_and_follow_ups():
    cards = _three_cards()
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(
            suggestions=cards, session_id="sess-9", meal_slot="breakfast"
        )
    )
    follow_ups = _FakeFollowUps(
        [
            {"label": "What's left?", "action": "remaining_budget"},
            {"label": "More breakfast ideas", "action": "next_meal"},
        ]
    )
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, next_meals=next_meals, follow_ups=follow_ups)

    events = await _stream(orchestrator, intent="next_meal")
    completed = next(event for event in events if event.event == "message.completed")

    assert completed.data["suggestions"] == cards
    assert completed.data["follow_ups"] == follow_ups.chips
    assert completed.data["intent"] == "next_meal"
    assert repo.completed["reply_payload"]["suggestions"] == cards
    assert repo.completed["reply_payload"]["discover_session_id"] == "sess-9"
    assert repo.completed["reply_payload"]["intent"] == "next_meal"
    assert next_meals.calls[0]["session_id"] is None


@pytest.mark.asyncio
async def test_next_meal_reuses_discover_session_id():
    prior = ChatMessage(
        id="m-prior",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.COMPLETED,
        created_at=utc_now(),
        updated_at=utc_now(),
        content="Try breakfast.",
        reply_payload={"discover_session_id": "sess-1", "suggestions": []},
    )
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(
            suggestions=[], session_id="sess-1", meal_slot="breakfast"
        )
    )
    repo = _FakeRepo(claim=_claim(), history=[prior])
    orchestrator = _orchestrator(repo, next_meals=next_meals)

    await _stream(orchestrator, intent="next_meal", content="More breakfast ideas")
    assert next_meals.calls[0]["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_remaining_budget_never_calls_discover():
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=_three_cards(), session_id="x")
    )
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, next_meals=next_meals)

    events = await _stream(
        orchestrator, intent="remaining_budget", content="What's left?"
    )
    completed = next(event for event in events if event.event == "message.completed")
    assert next_meals.calls == []
    assert completed.data["suggestions"] == []


@pytest.mark.asyncio
async def test_follow_up_failure_persists_empty_chips():
    follow_ups = _FakeFollowUps(error=TimeoutError())
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, follow_ups=follow_ups)

    events = await _stream(orchestrator, intent="day_progress")
    completed = next(event for event in events if event.event == "message.completed")
    assert completed.data["follow_ups"] == []
    assert repo.completed["reply_payload"]["follow_ups"] == []
