import asyncio

import pytest

from src.app.services.chat_next_meal_candidates import NextMealCandidateResult
from src.app.services.chat_turn_orchestrator import ChatTurnOrchestrator
from src.domain.exceptions.chat_exceptions import (
    ChatBusyError,
    ChatProviderUnavailableError,
    ChatRateLimitedError,
    ChatRegenerateNotAllowedError,
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
)
from src.domain.services.chat.policy import out_of_scope_message
from src.domain.utils.timezone_utils import utc_now
from src.infra.services.chat_concurrency import reset_chat_concurrency_for_tests


class _FakeRepo:
    def __init__(self, claim=None, history=None, turns_used=0, counted_keys=None):
        self.claim = claim
        self.history = history or []
        self.turns_used = turns_used
        self.counted_keys = set(counted_keys or ())
        self.completed = None
        self.failed = None
        self.cleared = False
        self.reopened = None
        self.reopen_result = None

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

    async def attach_follow_ups(self, **kwargs):
        self.attached_follow_ups = kwargs

    async def save_reply_suggestions(self, **kwargs):
        self.saved_suggestions = kwargs

    async def save_thread_summary(self, **kwargs):
        self.saved_summary = kwargs

    async def fail_assistant_message(self, **kwargs):
        self.failed = kwargs
        return self.claim.assistant_message

    async def reopen_assistant_message(self, **kwargs):
        self.reopened = kwargs
        if self.reopen_result is None:
            return None
        claim, intent = self.reopen_result
        requested = (kwargs.get("content") or "").strip()
        stored = (claim.user_message.content or "").strip()
        changed = bool(requested) and requested != stored
        if changed:
            claim = ChatTurnClaim(
                kind=claim.kind,
                thread=claim.thread,
                user_message=ChatMessage(
                    id=claim.user_message.id,
                    thread_id=claim.user_message.thread_id,
                    role=claim.user_message.role,
                    status=claim.user_message.status,
                    created_at=claim.user_message.created_at,
                    updated_at=claim.user_message.updated_at,
                    content=requested,
                    idempotency_key=claim.user_message.idempotency_key,
                    request_fingerprint=claim.user_message.request_fingerprint,
                ),
                assistant_message=claim.assistant_message,
            )
        return claim, intent, changed

    async def count_user_turns_since(self, **kwargs):
        used = self.turns_used
        excluded = kwargs.get("exclude_idempotency_key")
        if excluded and excluded in self.counted_keys:
            return max(0, used - 1)
        return used

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


class _RecordingCompletion:
    def __init__(self, chunks: list[str]):
        self.chunks = chunks
        self.kwargs: dict | None = None

    async def stream(self, **kwargs):
        self.kwargs = kwargs
        for chunk in self.chunks:
            yield ChatCompletionDelta(text=chunk)
        yield ChatCompletionDelta(
            text="",
            usage=ChatUsage(input_tokens=10, output_tokens=4, model="gpt-5.6-luna"),
            done=True,
        )


class _FakeCompletion:
    def __init__(self, chunks: list[str], tool_calls=None):
        self.chunks = chunks
        self.tool_calls = tool_calls
        self.stream_calls = 0
        self.tools = None

    async def stream(self, **kwargs):
        self.stream_calls += 1
        self.tools = kwargs.get("tools")
        if self.tool_calls and self.stream_calls == 1:
            yield ChatCompletionDelta(
                text="",
                tool_calls=self.tool_calls,
                done=True,
                usage=ChatUsage(input_tokens=10, output_tokens=4, model="gpt-5.6-luna"),
            )
            return

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
    def __init__(self):
        self.calls: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.1, 0.2]


class _FakeRetrieval:
    def __init__(self):
        self.calls: list[dict] = []

    async def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        return []


class _FakeContext:
    def __init__(self):
        self.calls: list[dict] = []

    async def build(self, **kwargs) -> ChatUserContext:
        self.calls.append(kwargs)
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
            food_calories=1150,
            movement_kcal_burned=0,
            local_date="2026-09-01",
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
    reply_payload=None,
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
        generation_id="gen-1",
        reply_payload=reply_payload,
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
    embedding=None,
    retrieval=None,
    context_builder=None,
):
    repo.turns_used = turns_used
    uow = _FakeUow(repo)
    embedding = embedding or _FakeEmbedding()
    retrieval = retrieval or _FakeRetrieval()
    context_builder = context_builder or _FakeContext()

    orchestrator = ChatTurnOrchestrator(
        completion=completion
        or _FakeCompletion(["Nutree has 650 calories remaining. "]),
        embedding=embedding,
        retrieval=retrieval,
        context_builder=context_builder,
        uow_factory=lambda: uow,
        model="gpt-5.6-luna",
        daily_turn_budget=40,
        generation_lease_seconds=90,
        global_concurrency=2,
        circuit_breaker=circuit_breaker,
        next_meals=next_meals,
        follow_ups=follow_ups,
    )
    orchestrator.test_embedding = embedding  # type: ignore[attr-defined]
    orchestrator.test_retrieval = retrieval  # type: ignore[attr-defined]
    orchestrator.test_context = context_builder  # type: ignore[attr-defined]
    return orchestrator


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
    assert repo.completed["reply_payload"]["suggestions"] == []
    assert repo.completed["reply_payload"]["follow_ups"] == []
    completed = next(event for event in events if event.event == "message.completed")
    assert completed.data["suggestions"] == []
    assert [chip["action"] for chip in completed.data["follow_ups"]] == [
        "remaining_budget",
        "day_progress",
    ]
    assert completed.data["nutrition_snapshot"]["food_calories"] == 1150
    assert completed.data["nutrition_snapshot"]["movement_kcal_burned"] == 0
    assert (
        repo.completed["reply_payload"]["nutrition_snapshot"]["remaining_calories"]
        == 650
    )


@pytest.mark.asyncio
async def test_words_stream_before_a_calorie_number_is_confirmed():
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

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    early = [
        event.data.get("delta", "")
        for event in events
        if event.event == "message.delta"
    ]
    assert early == ["Nutree has "]
    assert "650" not in "".join(early)
    completion.release.set()
    await asyncio.wait_for(task, timeout=2)
    deltas = [
        event.data.get("delta", "")
        for event in events
        if event.event == "message.delta"
    ]
    assert "".join(deltas) == "Nutree has 650 calories remaining. "
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
async def test_daily_budget_allows_replay_of_last_turn():
    claim = _claim(
        kind=ChatClaimKind.REPLAY,
        assistant_content="Nutree has 650 remaining.",
        status=ChatMessageStatus.COMPLETED,
    )
    repo = _FakeRepo(claim=claim, turns_used=40, counted_keys={"key-1"})
    orchestrator = _orchestrator(repo, turns_used=40)
    prepared = await orchestrator.prepare_turn(
        user_id="u1",
        content="How much is left?",
        idempotency_key="key-1",
        locale="en",
        header_timezone="UTC",
        user_language="en",
    )
    assert prepared.claim.kind == ChatClaimKind.REPLAY


@pytest.mark.asyncio
async def test_stale_generation_does_not_emit_completed():
    class _StaleRepo(_FakeRepo):
        async def complete_assistant_message(self, **kwargs):
            self.completed = kwargs
            return None

    repo = _StaleRepo(claim=_claim())
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
    assert events[-1].event == "message.error"
    assert events[-1].data["code"] == "CHAT_TURN_FAILED"
    assert repo.completed is not None
    assert repo.completed["generation_id"] == "gen-1"


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
async def test_replay_keeps_original_citation_label():
    claim = _claim(
        kind=ChatClaimKind.REPLAY,
        assistant_content="Stay at fiber [K2].",
        status=ChatMessageStatus.COMPLETED,
        citation_source_keys=("fiber-guide",),
        reply_payload={
            "suggestions": [],
            "follow_ups": [],
            "citation_refs": [{"label": "[K2]", "source_key": "fiber-guide"}],
        },
    )
    repo = _FakeRepo(claim=claim)
    repo.citation_metadata = {"fiber-guide": ("Fiber", None)}
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
            "label": "[K2]",
            "source_key": "fiber-guide",
            "title": "Fiber",
            "canonical_uri": None,
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
        reply_payload={
            "suggestions": [],
            "follow_ups": [],
            "intent": "remaining_budget",
            "nutrition_snapshot": {
                "version": "chat_nutrition_v1",
                "food_calories": 0,
                "remaining_calories": 1932,
            },
        },
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
    assert payload["messages"][0]["intent"] == "remaining_budget"
    assert payload["messages"][0]["nutrition_snapshot"]["remaining_calories"] == 1932


@pytest.mark.asyncio
async def test_get_thread_orders_equal_timestamp_user_before_assistant():
    """Turn pairs share claim-time created_at; UUID reverse must not flip them."""
    now = utc_now()
    # Assistant UUID sorts after user UUID under id.desc → reversed(page) would
    # put assistant first — the staging bug for "View today's progress".
    assistant = ChatMessage(
        id="aeb4c7f8-e3d6-4e2a-bd52-c1fd6d446569",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        content="You're on track today.",
        reply_payload={"intent": "day_progress"},
    )
    user = ChatMessage(
        id="0223f710-f7ee-41e1-8c5c-156d752819a1",
        thread_id="t1",
        role=ChatMessageRole.USER,
        status=ChatMessageStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        content="View today's progress",
    )
    claim = _claim()
    # Fake repo returns newest-first (assistant then user when id.desc).
    repo = _FakeRepo(claim=claim, history=[user, assistant])
    orchestrator = _orchestrator(repo)

    payload = await orchestrator.get_thread(user_id="u1", limit=50, before=None)

    assert [m["role"] for m in payload["messages"]] == ["user", "assistant"]
    assert payload["messages"][0]["content"] == "View today's progress"
    assert payload["messages"][1]["content"] == "You're on track today."
    assert payload["welcome"] is None


@pytest.mark.asyncio
async def test_empty_thread_includes_welcome_from_context():
    repo = _FakeRepo(claim=_claim(), history=[])
    orchestrator = _orchestrator(repo)

    payload = await orchestrator.get_thread(
        user_id="u1",
        limit=50,
        before=None,
        locale="vi",
        header_timezone="Asia/Ho_Chi_Minh",
    )

    assert payload["messages"] == []
    assert payload["welcome"]["remaining_calories"] == 650
    assert payload["welcome"]["remaining_protein_g"] == 50
    assert payload["welcome"]["suggested_meal_slot"] == "breakfast"
    assert orchestrator.test_context.calls[-1]["locale"] == "vi"
    assert orchestrator.test_context.calls[-1]["header_timezone"] == "Asia/Ho_Chi_Minh"


@pytest.mark.asyncio
async def test_empty_thread_omits_welcome_when_context_fails_or_paging():
    class _BrokenContext:
        async def build(self, **kwargs):
            del kwargs
            raise RuntimeError("context down")

    repo = _FakeRepo(claim=_claim(), history=[])
    orchestrator = _orchestrator(repo, context_builder=_BrokenContext())
    failed = await orchestrator.get_thread(user_id="u1", limit=50, before=None)
    assert failed["welcome"] is None

    paged = await _orchestrator(repo).get_thread(user_id="u1", limit=50, before="m1")
    assert paged["welcome"] is None


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
    completion = _FakeCompletion(["Here are suggestions. "])
    orchestrator = _orchestrator(
        repo, next_meals=next_meals, follow_ups=follow_ups, completion=completion
    )

    events = await _stream(orchestrator, intent=None)
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
    completion = _FakeCompletion(["Here you go. "])
    orchestrator = _orchestrator(repo, next_meals=next_meals, completion=completion)

    await _stream(orchestrator, intent=None, content="More breakfast ideas")
    assert next_meals.calls[0]["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_meal_refinement_refreshes_cards_from_the_previous_meals():
    prior = ChatMessage(
        id="m-prior",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.COMPLETED,
        created_at=utc_now(),
        updated_at=utc_now(),
        content="Try the tofu bowl.",
        reply_payload={
            "intent": "next_meal",
            "suggestions": [{"name": "Tofu bowl", "calories": 420}],
        },
    )
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=_three_cards(), meal_slot="lunch")
    )
    repo = _FakeRepo(claim=_claim(), history=[prior])
    completion = _FakeCompletion(["Updated. "])
    orchestrator = _orchestrator(repo, next_meals=next_meals, completion=completion)

    events = await _stream(orchestrator, content="Make the tofu higher protein")
    completed = next(event for event in events if event.event == "message.completed")

    assert next_meals.calls
    assert "Tofu bowl" in next_meals.calls[0]["user_text"]
    assert "higher protein" in next_meals.calls[0]["user_text"]
    assert completed.data["intent"] == "next_meal"
    assert completed.data["suggestions"] == _three_cards()
    replaced = [
        str(event.data.get("delta") or "")
        for event in events
        if event.event == "message.delta" and event.data.get("replace")
    ]
    assert any("higher-protein" in text for text in replaced)


@pytest.mark.asyncio
async def test_why_question_after_a_meal_does_not_refresh_cards():
    prior = ChatMessage(
        id="m-prior",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.COMPLETED,
        created_at=utc_now(),
        updated_at=utc_now(),
        content="Try the tofu bowl.",
        reply_payload={
            "intent": "next_meal",
            "suggestions": [{"name": "Tofu bowl", "calories": 420}],
        },
    )
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=_three_cards(), meal_slot="lunch")
    )
    repo = _FakeRepo(claim=_claim(), history=[prior])
    completion = _FakeCompletion(["Lunch fits because protein is still open. "])
    orchestrator = _orchestrator(repo, next_meals=next_meals, completion=completion)

    events = await _stream(orchestrator, content="Why is lunch the better fit?")
    completed = next(event for event in events if event.event == "message.completed")

    assert next_meals.calls == []
    assert completed.data.get("intent") != "next_meal"


@pytest.mark.asyncio
async def test_comparison_question_does_not_refresh_meal_cards():
    prior = ChatMessage(
        id="m-prior",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.COMPLETED,
        created_at=utc_now(),
        updated_at=utc_now(),
        content="Try the tofu bowl.",
        reply_payload={
            "intent": "next_meal",
            "suggestions": [{"name": "Tofu bowl", "calories": 420}],
        },
    )
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=_three_cards(), meal_slot="lunch")
    )
    repo = _FakeRepo(claim=_claim(), history=[prior])
    completion = _FakeCompletion(["The tofu bowl has more protein. "])
    orchestrator = _orchestrator(repo, next_meals=next_meals, completion=completion)

    events = await _stream(orchestrator, content="Which of these is higher protein?")
    completed = next(event for event in events if event.event == "message.completed")

    assert next_meals.calls == []
    assert completed.data.get("intent") != "next_meal"
    assert completion.tools == []


@pytest.mark.asyncio
async def test_typed_dinner_fetches_next_meal_without_chip():
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=_three_cards(), meal_slot="dinner")
    )
    repo = _FakeRepo(claim=_claim())
    completion = _FakeCompletion(["Here you go. "])
    orchestrator = _orchestrator(repo, next_meals=next_meals, completion=completion)

    events = await _stream(orchestrator, content="What's for dinner?")
    completed = next(event for event in events if event.event == "message.completed")
    assert next_meals.calls
    assert completed.data["intent"] == "next_meal"
    assert completed.data["suggestions"] == _three_cards()


@pytest.mark.asyncio
async def test_nutrition_free_text_still_streams_an_answer():
    completion = _FakeCompletion(["Protein stays at your Nutree target. "])
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, completion=completion)

    events = await _stream(orchestrator, content="Cite protein guidance")
    completed = next(event for event in events if event.event == "message.completed")
    assert completion.stream_calls == 1
    assert "intent" not in completed.data
    assert completed.data["suggestions"] == []
    assert "Protein stays" in repo.completed["content"]


@pytest.mark.asyncio
async def test_remaining_budget_never_calls_discover():
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=_three_cards(), session_id="x")
    )
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, next_meals=next_meals)

    events = await _stream(orchestrator, intent=None, content="What's left?")
    completed = next(event for event in events if event.event == "message.completed")
    assert next_meals.calls == []
    assert completed.data["suggestions"] == []


@pytest.mark.asyncio
async def test_follow_up_failure_uses_fallback_chips():
    follow_ups = _FakeFollowUps(error=TimeoutError())
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, follow_ups=follow_ups)

    events = await _stream(orchestrator, intent="day_progress")
    completed = next(event for event in events if event.event == "message.completed")
    assert [chip["action"] for chip in completed.data["follow_ups"]] == [
        "remaining_budget",
        "next_meal",
    ]
    assert repo.completed["reply_payload"]["follow_ups"] == []
    assert repo.attached_follow_ups["follow_ups"] == completed.data["follow_ups"]


def test_chat_orchestrator_tools_contains_suggest_next_meal():
    from src.app.services.chat_turn_orchestrator import CHAT_ORCHESTRATOR_TOOLS
    from src.domain.services.chat.coach_functions import openai_tools

    tool_names = [tool["function"]["name"] for tool in CHAT_ORCHESTRATOR_TOOLS]
    assert "suggest_next_meal" in tool_names
    assert set(tool_names) == {
        "suggest_next_meal",
        "check_daily_progress",
        "search_nutrition_knowledge",
        "explain_limits_and_guidelines",
    }
    assert CHAT_ORCHESTRATOR_TOOLS == openai_tools()


@pytest.mark.asyncio
async def test_remaining_budget_chip_skips_embed_and_retrieve():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo)

    await _stream(orchestrator, intent="remaining_budget", content="What's left?")

    assert orchestrator.test_embedding.calls == []
    assert orchestrator.test_retrieval.calls == []
    assert orchestrator.test_context.calls


@pytest.mark.asyncio
async def test_day_progress_chip_skips_embed_and_retrieve():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo)

    await _stream(orchestrator, intent="day_progress", content="Day progress")

    assert orchestrator.test_embedding.calls == []
    assert orchestrator.test_retrieval.calls == []


@pytest.mark.asyncio
async def test_limits_chip_skips_embed_and_retrieve():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo)

    events = await _stream(orchestrator, intent="limits", content="What can you do?")
    completed = next(event for event in events if event.event == "message.completed")

    assert orchestrator.test_embedding.calls == []
    assert orchestrator.test_retrieval.calls == []
    assert completed.data["intent"] == "limits"


@pytest.mark.asyncio
async def test_next_meal_chip_skips_retrieve_but_prefetches_card():
    cards = _three_cards()
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=cards, meal_slot="lunch")
    )
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, next_meals=next_meals)

    events = await _stream(orchestrator, intent="next_meal", content="Lunch ideas")
    completed = next(event for event in events if event.event == "message.completed")

    assert orchestrator.test_embedding.calls == []
    assert orchestrator.test_retrieval.calls == []
    assert next_meals.calls
    assert completed.data["suggestions"] == cards


@pytest.mark.asyncio
async def test_free_text_still_embeds_and_retrieves():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo)

    await _stream(orchestrator, intent=None, content="Cite protein guidance")

    assert orchestrator.test_embedding.calls
    assert orchestrator.test_retrieval.calls


@pytest.mark.asyncio
async def test_meal_recommendation_generates_upfront_with_intent_chip():
    """A next-meal chip uses the cards for the intro and does not call the chat model."""
    cards = _three_cards()
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=cards, meal_slot="lunch")
    )
    repo = _FakeRepo(claim=_claim())
    completion = _FakeCompletion(["Dưới đây là gợi ý bữa trưa hấp dẫn cho bạn! "])
    orchestrator = _orchestrator(repo, next_meals=next_meals, completion=completion)

    events = await _stream(
        orchestrator,
        content="Hôm nay ăn gì cho bữa trưa?",
        intent="next_meal",
    )
    completed = next(event for event in events if event.event == "message.completed")

    assert completion.stream_calls == 0
    assert next_meals.calls
    assert "Egg rice bowl" in repo.completed["content"]
    assert "420" not in repo.completed["content"]
    assert completed.data["intent"] == "next_meal"
    assert completed.data["suggestions"] == cards
    assert repo.completed["reply_payload"]["suggestions"] == cards


@pytest.mark.asyncio
async def test_typed_meal_question_fetches_cards_without_a_tool_call():
    cards = _three_cards()
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=cards, meal_slot="breakfast")
    )
    repo = _FakeRepo(claim=_claim())
    completion = _FakeCompletion(["Oatmeal fits the rest of your day. "])
    orchestrator = _orchestrator(repo, next_meals=next_meals, completion=completion)

    events = await _stream(orchestrator, intent=None, content="What should I eat next?")
    completed = next(event for event in events if event.event == "message.completed")

    assert next_meals.calls
    assert orchestrator.test_embedding.calls == []
    assert orchestrator.test_retrieval.calls == []
    assert completed.data["intent"] == "next_meal"
    assert completed.data["suggestions"] == cards
    assert completion.stream_calls == 0
    assert "Egg rice bowl" in repo.completed["content"]
    assert "Yogurt cup and Tofu scramble" in repo.completed["content"]
    assert "Oatmeal fits" not in repo.completed["content"]


@pytest.mark.asyncio
async def test_meal_ack_streams_before_cards_are_ready():
    class _SlowMeals:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def fetch(self, **kwargs):
            del kwargs
            self.started.set()
            await self.release.wait()
            return NextMealCandidateResult(
                suggestions=_three_cards(), meal_slot="dinner"
            )

    meals = _SlowMeals()
    orchestrator = _orchestrator(_FakeRepo(claim=_claim()), next_meals=meals)
    events: list = []

    async def collect() -> None:
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="What should I eat next?",
            idempotency_key="key-1",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        ):
            events.append(event)

    task = asyncio.create_task(collect())
    await asyncio.wait_for(meals.started.wait(), timeout=2)
    deltas = [
        event.data.get("delta", "")
        for event in events
        if event.event == "message.delta"
    ]
    assert any("I'm picking" in delta for delta in deltas)
    assert all("Egg rice bowl" not in delta for delta in deltas)
    meals.release.set()
    await task
    assert any("Egg rice bowl" in event.data.get("delta", "") for event in events)


@pytest.mark.asyncio
async def test_meal_intro_is_sent_before_food_photos():
    class _Meals:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def fetch(self, **kwargs):
            assert kwargs["include_images"] is False
            self.events.append("fetch")
            return NextMealCandidateResult(
                suggestions=_three_cards(), meal_slot="dinner"
            )

        async def attach_images(self, suggestions):
            self.events.append("images")
            return [
                {**card, "thumbnail_url": "https://cdn.example/meal.jpg"}
                for card in suggestions
            ]

    meals = _Meals()
    orchestrator = _orchestrator(_FakeRepo(claim=_claim()), next_meals=meals)
    events = []
    intro_seen = False
    async for event in orchestrator.stream_turn(
        user_id="u1",
        content="What should I eat next?",
        idempotency_key="key-1",
        locale="en",
        header_timezone="UTC",
        user_language="en",
    ):
        if event.event == "message.delta" and not intro_seen:
            assert meals.events == []
            assert "I'm picking" in event.data.get("delta", "")
            intro_seen = True
        elif event.event == "message.delta" and "Egg rice bowl" in event.data.get(
            "delta", ""
        ):
            assert meals.events == ["fetch"]
        elif event.event == "message.ready":
            assert "images" not in meals.events
        events.append(event)
    completed = next(event for event in events if event.event == "message.completed")
    assert intro_seen
    assert meals.events == ["fetch", "images"]
    named = next(
        event
        for event in events
        if event.event == "message.delta" and event.data.get("replace") is True
    )
    assert "Egg rice bowl" in named.data["delta"]
    assert "I'm picking" not in named.data["delta"]
    assert (
        completed.data["suggestions"][0]["thumbnail_url"]
        == "https://cdn.example/meal.jpg"
    )


@pytest.mark.asyncio
async def test_provider_drop_keeps_the_partial_reply():
    class _DropAfterSentence:
        async def stream(self, **kwargs):
            del kwargs
            yield ChatCompletionDelta(text="Here is a start. ")
            raise RuntimeError("socket dropped")

    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, completion=_DropAfterSentence())
    events = await _stream(orchestrator, content="Cite protein guidance")
    assert events[-1].event == "message.error"
    assert repo.completed is not None
    assert "Here is a start" in repo.completed["content"]
    assert repo.failed is None


@pytest.mark.asyncio
async def test_cancel_persists_partial_reply_and_releases_the_turn():
    class _CancelAfterSentence:
        async def stream(self, **kwargs):
            del kwargs
            yield ChatCompletionDelta(text="Here is a start. ")
            raise asyncio.CancelledError()

    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, completion=_CancelAfterSentence())

    with pytest.raises(asyncio.CancelledError):
        await _stream(orchestrator, content="Cite protein guidance")

    assert repo.failed is None
    assert repo.completed is not None
    assert "Here is a start" in repo.completed["content"]


@pytest.mark.asyncio
async def test_words_stream_before_the_sentence_ends():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(
        repo, completion=_FakeCompletion(["Hello ", "there. "])
    )
    events = await _stream(orchestrator, content="Cite protein guidance")
    deltas = [
        event.data.get("delta", "")
        for event in events
        if event.event == "message.delta"
    ]
    assert "Hello " in deltas
    assert any("there." in delta for delta in deltas)


@pytest.mark.asyncio
async def test_regenerate_reuses_the_stored_user_message():
    claim = _claim()
    repo = _FakeRepo(claim=claim)
    repo.reopen_result = (claim, "next_meal")
    completion = _FakeCompletion(["Another idea. "])
    orchestrator = _orchestrator(repo, completion=completion)

    prepared = await orchestrator.prepare_regeneration(
        user_id="u1",
        assistant_message_id="m-asst",
        content="How much is left?",
        locale="en",
        header_timezone="UTC",
        user_language="en",
    )
    events = [
        event
        async for event in orchestrator.stream_prepared(user_id="u1", prepared=prepared)
    ]
    completed = next(event for event in events if event.event == "message.completed")
    assert repo.reopened["assistant_message_id"] == "m-asst"
    assert repo.reopened["content"] == "How much is left?"
    assert prepared.content == "How much is left?"
    assert completed.data["intent"] == "next_meal"
    assert "Another idea" in repo.completed["content"]


@pytest.mark.asyncio
async def test_regenerate_with_new_text_edits_the_stored_question():
    claim = _claim()
    repo = _FakeRepo(claim=claim)
    repo.reopen_result = (claim, None)
    completion = _FakeCompletion(["Soup fits better. "])
    orchestrator = _orchestrator(repo, completion=completion)

    prepared = await orchestrator.prepare_regeneration(
        user_id="u1",
        assistant_message_id="m-asst",
        content="What about soup?",
        locale="en",
        header_timezone="UTC",
        user_language="en",
    )
    events = [
        event
        async for event in orchestrator.stream_prepared(user_id="u1", prepared=prepared)
    ]
    completed = next(event for event in events if event.event == "message.completed")
    assert prepared.content == "What about soup?"
    assert "Soup fits" in completed.data.get("text", repo.completed["content"])
    assert "Soup fits" in repo.completed["content"]


@pytest.mark.asyncio
async def test_edited_question_drops_the_previous_meal_intent():
    claim = _claim()
    repo = _FakeRepo(claim=claim)
    repo.reopen_result = (claim, "next_meal")
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=_three_cards(), meal_slot="dinner")
    )
    completion = _FakeCompletion(["Protein stays at your Nutree target. "])
    orchestrator = _orchestrator(repo, completion=completion, next_meals=next_meals)

    prepared = await orchestrator.prepare_regeneration(
        user_id="u1",
        assistant_message_id="m-asst",
        content="Cite protein guidance",
        locale="en",
        header_timezone="UTC",
        user_language="en",
    )
    events = [
        event
        async for event in orchestrator.stream_prepared(user_id="u1", prepared=prepared)
    ]
    completed = next(event for event in events if event.event == "message.completed")
    assert prepared.intent is None
    assert next_meals.calls == []
    assert completed.data.get("intent") is None
    assert completed.data["suggestions"] == []


@pytest.mark.asyncio
async def test_regenerate_rejects_a_reply_that_cannot_be_reopened():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo)
    with pytest.raises(ChatRegenerateNotAllowedError):
        await orchestrator.prepare_regeneration(
            user_id="u1",
            assistant_message_id="missing",
            content="How much is left?",
            locale="en",
            header_timezone=None,
            user_language="en",
        )


def test_history_keeps_the_latest_forty_turns():
    from src.app.services.chat_turn_orchestrator import _history_turns
    from src.domain.model.chat import CHAT_HISTORY_LIMIT

    now = utc_now()
    messages = [
        ChatMessage(
            id=f"m{index}",
            thread_id="t1",
            role=ChatMessageRole.USER,
            status=ChatMessageStatus.COMPLETED,
            created_at=now,
            updated_at=now,
            content=f"turn {index}",
        )
        for index in range(50)
    ]
    turns = _history_turns(messages)
    assert len(turns) == CHAT_HISTORY_LIMIT
    assert turns[0].content == "turn 10"
    assert turns[-1].content == "turn 49"


@pytest.mark.asyncio
async def test_saved_summary_is_included_before_the_live_window():
    from dataclasses import replace

    claim = _claim()
    claim = replace(
        claim,
        thread=replace(claim.thread, summary="User: no cilantro"),
    )
    completion = _RecordingCompletion(["Noted. "])
    orchestrator = _orchestrator(
        _FakeRepo(claim=claim, history=[]),
        completion=completion,
    )
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="What can I cook tonight?",
            idempotency_key="key-1",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        )
    ]
    assert any(event.event == "message.completed" for event in events)
    assert completion.kwargs is not None
    assert "no cilantro" in completion.kwargs["grounding_message"]


@pytest.mark.asyncio
async def test_older_turns_are_sent_as_memory_not_live_history():
    from src.domain.model.chat import CHAT_HISTORY_LIMIT

    now = utc_now()
    history = [
        ChatMessage(
            id=f"old-{index}",
            thread_id="t1",
            role=ChatMessageRole.USER,
            status=ChatMessageStatus.COMPLETED,
            created_at=now,
            updated_at=now,
            content=f"prefer no cilantro {index}",
        )
        for index in range(CHAT_HISTORY_LIMIT + 2)
    ]
    completion = _RecordingCompletion(["Noted. "])
    orchestrator = _orchestrator(
        _FakeRepo(claim=_claim(), history=history),
        completion=completion,
    )
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="What can I cook tonight?",
            idempotency_key="key-1",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        )
    ]
    assert any(event.event == "message.completed" for event in events)
    assert completion.kwargs is not None
    assert len(completion.kwargs["history"]) == CHAT_HISTORY_LIMIT
    grounding = completion.kwargs["grounding_message"]
    assert "prefer no cilantro 0" in grounding
    assert "EARLIER CONVERSATION" in grounding
    assert "prefer no cilantro 0" not in completion.kwargs["history"][0].content


@pytest.mark.asyncio
async def test_reply_is_saved_before_follow_up_chips():
    repo = _FakeRepo(claim=_claim())

    class _FollowUps:
        def __init__(self) -> None:
            self.saved_first = False

        async def generate_follow_ups(self, **kwargs):
            del kwargs
            self.saved_first = repo.completed is not None
            return [{"label": "Another idea", "action": "next_meal"}]

    follow_ups = _FollowUps()
    orchestrator = _orchestrator(
        repo,
        completion=_FakeCompletion(["Fiber helps a meal last longer. "]),
        follow_ups=follow_ups,
    )
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="Help me compare two options",
            idempotency_key="key-1",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        )
    ]
    names = [event.event for event in events]
    assert names.index("message.ready") < names.index("message.completed")
    ready = next(event for event in events if event.event == "message.ready")
    completed = next(event for event in events if event.event == "message.completed")
    assert "follow_ups" not in ready.data
    assert completed.data["follow_ups"][0]["label"] == "Another idea"
    assert follow_ups.saved_first
    assert repo.attached_follow_ups["follow_ups"][0]["label"] == "Another idea"


@pytest.mark.asyncio
async def test_follow_up_can_quote_the_previous_meal_card():
    now = utc_now()
    prior = ChatMessage(
        id="a-prior",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        content="Try this.",
        reply_payload={
            "suggestions": [
                {
                    "name": "Salmon rice",
                    "calories": 420,
                    "protein_g": 32,
                    "carbs_g": 40,
                    "fat_g": 11,
                }
            ]
        },
    )
    repo = _FakeRepo(claim=_claim(), history=[prior])
    orchestrator = _orchestrator(
        repo, completion=_FakeCompletion(["That portion is 420 calories. "])
    )
    events = await _stream(orchestrator, content="How many calories was that meal?")
    deltas = "".join(
        event.data.get("delta", "")
        for event in events
        if event.event == "message.delta"
    )
    assert "420" in deltas
    assert events[-1].event == "message.completed"


def test_history_remembers_the_meal_card_macros():
    from src.app.services.chat_turn_orchestrator import _history_turns

    now = utc_now()
    message = ChatMessage(
        id="a1",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        content="Try this.",
        reply_payload={
            "suggestions": [
                {
                    "name": "Salmon rice",
                    "calories": 420,
                    "protein_g": 32,
                    "carbs_g": 40,
                    "fat_g": 11,
                }
            ]
        },
    )
    turns = _history_turns([message])
    assert "Meal card shown: Salmon rice." in turns[0].content
    assert "420 kcal" in turns[0].content
    assert "32g protein" in turns[0].content


def test_history_remembers_every_shown_meal_card():
    from src.app.services.chat_turn_orchestrator import _history_turns

    now = utc_now()
    message = ChatMessage(
        id="a1",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        content="Try one of these.",
        reply_payload={
            "suggestions": [
                {
                    "name": "Salmon rice",
                    "calories": 420,
                    "protein_g": 32,
                    "carbs_g": 40,
                    "fat_g": 11,
                },
                {
                    "name": "Tofu bowl",
                    "calories": 380,
                    "protein_g": 28,
                    "carbs_g": 35,
                    "fat_g": 14,
                },
            ]
        },
    )
    turns = _history_turns([message])
    assert "Salmon rice" in turns[0].content
    assert "32g protein" in turns[0].content
    assert "Tofu bowl" in turns[0].content
    assert "28g protein" in turns[0].content


def test_history_remembers_the_meal_card_name():
    from src.app.services.chat_turn_orchestrator import _history_turns

    now = utc_now()
    message = ChatMessage(
        id="a1",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        content="Try this.",
        reply_payload={"suggestions": [{"name": "Salmon rice"}]},
    )
    turns = _history_turns([message])
    assert turns[0].content == "Try this.\nMeal card shown: Salmon rice."
