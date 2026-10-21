import asyncio

import pytest

from src.app.services.chat_next_meal_candidates import NextMealCandidateResult
from src.app.services.chat_turn_orchestrator import (
    ChatTurnOrchestrator,
    PreparedChatTurn,
)
from src.domain.exceptions.chat_exceptions import (
    ChatBusyError,
    ChatInvalidCursorError,
    ChatProviderUnavailableError,
    ChatRateLimitedError,
    ChatRegenerateNotAllowedError,
    ChatThreadNotFoundError,
)
from src.domain.model.chat import (
    CHAT_MAX_OUTPUT_TOKENS,
    CHAT_OUTPUT_TOKEN_CEILING,
    ChatClaimKind,
    ChatCompletionDelta,
    ChatMealSummary,
    ChatMessage,
    ChatMessageRole,
    ChatMessageStatus,
    ChatThread,
    ChatThreadListing,
    ChatTurnClaim,
    ChatUsage,
    ChatUserContext,
)
from src.domain.services.chat.thread_listing import (
    decode_thread_cursor,
    encode_thread_cursor,
)
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

    async def get_thread(self, user_id: str, thread_id: str | None = None):
        self.got_thread_id = thread_id
        if thread_id is not None and thread_id != self.claim.thread.id:
            return None
        return self.claim.thread

    async def create_thread(self, user_id: str) -> ChatThread:
        return self.claim.thread

    async def list_threads(self, **kwargs):
        self.list_kwargs = kwargs
        return list(getattr(self, "listings", []))

    async def claim_turn(self, **kwargs):
        self.claim_kwargs = kwargs
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

    async def clear_thread(self, user_id: str, thread_id: str | None = None):
        self.cleared = True
        self.cleared_thread_id = thread_id
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
    def __init__(self, recent_meals=()):
        self.calls: list[dict] = []
        self.recent_meals = recent_meals

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
            recent_meals=self.recent_meals,
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
async def test_safety_cutoff_does_not_persist_a_dangling_fragment():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(
        repo,
        completion=_FakeCompletion(["Nếu ", "dư 24000 kcal mỗi ngày. "]),
    )
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="Ăn dư 200 calo trong 4 tháng lên mấy cân",
            idempotency_key="key-1",
            locale="vi",
            header_timezone="UTC",
            user_language="vi",
        )
    ]
    assert repo.completed is not None
    assert repo.completed["content"] != "Nếu"
    assert "24000" not in repo.completed["content"]
    assert "Nutree đã ghi" in repo.completed["content"]
    assert any(
        event.event == "message.delta" and event.data.get("replace") is True
        for event in events
    )


@pytest.mark.asyncio
async def test_user_stated_surplus_is_streamed():
    repo = _FakeRepo(claim=_claim())
    answer = "Dư 200 kcal vẫn ổn so với mục tiêu 1800 calories. "
    orchestrator = _orchestrator(repo, completion=_FakeCompletion([answer]))
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="Ngày tôi ăn dư 200calo có sao không",
            idempotency_key="key-1",
            locale="vi",
            header_timezone="UTC",
            user_language="vi",
        )
    ]
    deltas = "".join(
        event.data.get("delta", "")
        for event in events
        if event.event == "message.delta"
    )
    assert "200 kcal" in deltas
    assert repo.completed is not None
    assert "200 kcal" in repo.completed["content"]


@pytest.mark.asyncio
async def test_truncated_reasoning_budget_retries_with_a_higher_cap():
    class _TruncatingThenFull:
        def __init__(self):
            self.budgets: list[int] = []

        async def stream(self, **kwargs):
            self.budgets.append(kwargs["max_output_tokens"])
            if len(self.budgets) == 1:
                yield ChatCompletionDelta(text="Nếu")
                yield ChatCompletionDelta(
                    text="",
                    done=True,
                    truncated=True,
                    usage=ChatUsage(
                        input_tokens=10, output_tokens=900, model="gpt-5.6-luna"
                    ),
                )
                return
            yield ChatCompletionDelta(
                text="Dư 200 kcal trong bốn tháng là khoảng vài cân. "
            )
            yield ChatCompletionDelta(
                text="",
                done=True,
                usage=ChatUsage(
                    input_tokens=10, output_tokens=40, model="gpt-5.6-luna"
                ),
            )

    repo = _FakeRepo(claim=_claim())
    completion = _TruncatingThenFull()
    orchestrator = _orchestrator(repo, completion=completion)
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="Ăn ngày dư 200 trong 4 tháng lên mấy cân",
            idempotency_key="key-1",
            locale="vi",
            header_timezone="UTC",
            user_language="vi",
        )
    ]
    assert completion.budgets == [
        CHAT_MAX_OUTPUT_TOKENS,
        min(CHAT_MAX_OUTPUT_TOKENS * 2, CHAT_OUTPUT_TOKEN_CEILING),
    ]
    assert repo.completed is not None
    assert "vài cân" in repo.completed["content"]
    assert repo.completed["usage"].input_tokens == 20
    assert repo.completed["usage"].output_tokens == 940
    assert any(event.data.get("replace") is True for event in events)


@pytest.mark.asyncio
async def test_truncated_empty_output_retries_instead_of_fallback():
    class _ReasoningOnlyThenFull:
        def __init__(self):
            self.budgets: list[int] = []

        async def stream(self, **kwargs):
            self.budgets.append(kwargs["max_output_tokens"])
            if len(self.budgets) == 1:
                yield ChatCompletionDelta(
                    text="",
                    done=True,
                    truncated=True,
                    usage=ChatUsage(
                        input_tokens=12, output_tokens=4096, model="gpt-5.6-luna"
                    ),
                )
                return
            yield ChatCompletionDelta(
                text="Dư 200 kcal trong bốn tháng là khoảng vài cân. "
            )
            yield ChatCompletionDelta(
                text="",
                done=True,
                usage=ChatUsage(input_tokens=8, output_tokens=30, model="gpt-5.6-luna"),
            )

    repo = _FakeRepo(claim=_claim())
    completion = _ReasoningOnlyThenFull()
    orchestrator = _orchestrator(repo, completion=completion)
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="Ăn ngày dư 200 trong 4 tháng lên mấy cân",
            idempotency_key="key-1",
            locale="vi",
            header_timezone="UTC",
            user_language="vi",
        )
    ]
    assert completion.budgets == [
        CHAT_MAX_OUTPUT_TOKENS,
        min(CHAT_MAX_OUTPUT_TOKENS * 2, CHAT_OUTPUT_TOKEN_CEILING),
    ]
    assert repo.completed is not None
    assert "vài cân" in repo.completed["content"]
    assert "Tôi chỉ dùng số liệu" not in repo.completed["content"]
    assert repo.completed["usage"].input_tokens == 20
    assert repo.completed["usage"].output_tokens == 4126
    assert repo.completed["usage"].cached_tokens == 0
    assert events[-1].event == "message.completed"


@pytest.mark.asyncio
async def test_failed_output_limit_retry_keeps_the_first_pass():
    class _TruncatingThenDown:
        def __init__(self):
            self.budgets: list[int] = []

        async def stream(self, **kwargs):
            self.budgets.append(kwargs["max_output_tokens"])
            if len(self.budgets) == 1:
                yield ChatCompletionDelta(text="Không sao, chênh lệch")
                yield ChatCompletionDelta(
                    text="",
                    done=True,
                    truncated=True,
                    usage=ChatUsage(
                        input_tokens=10,
                        output_tokens=900,
                        cached_tokens=4,
                        model="gpt-5.6-luna",
                    ),
                )
                return
            raise RuntimeError("stream aborted")

    repo = _FakeRepo(claim=_claim())
    completion = _TruncatingThenDown()
    orchestrator = _orchestrator(repo, completion=completion)
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="Ăn ngày dư 200 trong 4 tháng lên mấy cân",
            idempotency_key="key-1",
            locale="vi",
            header_timezone="UTC",
            user_language="vi",
        )
    ]
    assert completion.budgets == [
        CHAT_MAX_OUTPUT_TOKENS,
        min(CHAT_MAX_OUTPUT_TOKENS * 2, CHAT_OUTPUT_TOKEN_CEILING),
    ]
    assert repo.completed is not None
    assert "Không sao, chênh lệch" in repo.completed["content"]
    assert repo.completed["usage"].input_tokens == 10
    assert repo.completed["usage"].output_tokens == 900
    assert repo.completed["usage"].cached_tokens == 4
    assert events[-1].event == "message.error"
    assert events[-1].data["code"] == "CHAT_PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_failed_retry_keeps_text_already_sent_to_the_client():
    class _PartialRetryThenDown:
        def __init__(self):
            self.calls = 0

        async def stream(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield ChatCompletionDelta(text="Nếu")
                yield ChatCompletionDelta(
                    text="",
                    done=True,
                    truncated=True,
                    usage=ChatUsage(
                        input_tokens=10, output_tokens=900, model="gpt-5.6-luna"
                    ),
                )
                return
            yield ChatCompletionDelta(text="Dư 200 kcal trong bốn tháng là ")
            yield ChatCompletionDelta(
                text="",
                usage=ChatUsage(input_tokens=6, output_tokens=20, model="gpt-5.6-luna"),
            )
            raise RuntimeError("stream aborted")

    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, completion=_PartialRetryThenDown())
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="Ăn ngày dư 200 trong 4 tháng lên mấy cân",
            idempotency_key="key-1",
            locale="vi",
            header_timezone="UTC",
            user_language="vi",
        )
    ]
    assert repo.completed is not None
    assert "Dư 200 kcal" in repo.completed["content"]
    assert repo.completed["content"] != "Nếu"
    assert repo.completed["usage"].input_tokens == 16
    assert repo.completed["usage"].output_tokens == 920
    assert any(
        event.event == "message.delta" and event.data.get("replace") is True
        for event in events
    )
    assert events[-1].event == "message.error"


@pytest.mark.asyncio
async def test_empty_retry_keeps_finished_sentences_from_the_first_pass():
    class _FinishedThenEmpty:
        def __init__(self):
            self.calls = 0

        async def stream(self, **kwargs):
            del kwargs
            self.calls += 1
            if self.calls == 1:
                yield ChatCompletionDelta(text="Xin lỗi, câu trước bị thiếu ý. Nếu")
                yield ChatCompletionDelta(
                    text="",
                    done=True,
                    truncated=True,
                    usage=ChatUsage(
                        input_tokens=10, output_tokens=900, model="gpt-5.6-luna"
                    ),
                )
                return
            yield ChatCompletionDelta(
                text="",
                done=True,
                truncated=True,
                usage=ChatUsage(
                    input_tokens=4, output_tokens=200, model="gpt-5.6-luna"
                ),
            )

    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, completion=_FinishedThenEmpty())
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="Hả",
            idempotency_key="key-1",
            locale="vi",
            header_timezone="UTC",
            user_language="vi",
        )
    ]
    assert repo.completed is not None
    assert "câu trước bị thiếu ý" in repo.completed["content"]
    assert "Tôi chỉ dùng số liệu" not in repo.completed["content"]
    assert repo.completed["content"].strip().endswith(".")
    assert "Nếu" not in repo.completed["content"]
    assert events[-1].event == "message.completed"


@pytest.mark.asyncio
async def test_output_limit_retry_does_not_offer_tools():
    class _TruncatingThenTools:
        def __init__(self):
            self.budgets: list[int] = []
            self.tools: list = []

        async def stream(self, **kwargs):
            self.budgets.append(kwargs["max_output_tokens"])
            self.tools.append(kwargs.get("tools"))
            if len(self.budgets) == 1:
                yield ChatCompletionDelta(text="Nếu")
                yield ChatCompletionDelta(
                    text="",
                    done=True,
                    truncated=True,
                    usage=ChatUsage(
                        input_tokens=10, output_tokens=900, model="gpt-5.6-luna"
                    ),
                )
                return
            if kwargs.get("tools"):
                yield ChatCompletionDelta(
                    text="",
                    done=True,
                    tool_calls=[
                        {
                            "id": "call_1",
                            "name": "suggest_next_meal",
                            "args": {},
                        }
                    ],
                )
                return
            yield ChatCompletionDelta(
                text="Dư 200 kcal trong bốn tháng là khoảng vài cân. "
            )
            yield ChatCompletionDelta(text="", done=True)

    repo = _FakeRepo(claim=_claim())
    completion = _TruncatingThenTools()
    orchestrator = _orchestrator(repo, completion=completion)
    async for _event in orchestrator.stream_turn(
        user_id="u1",
        content="Ăn ngày dư 200 trong 4 tháng lên mấy cân",
        idempotency_key="key-1",
        locale="vi",
        header_timezone="UTC",
        user_language="vi",
    ):
        pass
    assert completion.tools[0]
    assert completion.tools[1] in (None, [])
    assert repo.completed is not None
    assert "vài cân" in repo.completed["content"]
    assert "Tôi chỉ dùng số liệu" not in repo.completed["content"]


@pytest.mark.asyncio
async def test_lease_renew_failure_does_not_drop_the_retry():
    class _RenewFails:
        async def extend_generation_lease(self, **kwargs):
            del kwargs
            raise RuntimeError("db blip")

    class _TruncatingThenFull:
        def __init__(self):
            self.calls = 0

        async def stream(self, **kwargs):
            del kwargs
            self.calls += 1
            if self.calls == 1:
                yield ChatCompletionDelta(text="Nếu")
                yield ChatCompletionDelta(text="", done=True, truncated=True)
                return
            yield ChatCompletionDelta(
                text="Dư 200 kcal trong bốn tháng là khoảng vài cân. "
            )

    repo = _FakeRepo(claim=_claim())
    repo.extend_generation_lease = _RenewFails().extend_generation_lease
    orchestrator = _orchestrator(repo, completion=_TruncatingThenFull())
    events = [
        event
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="Ăn ngày dư 200 trong 4 tháng lên mấy cân",
            idempotency_key="key-1",
            locale="vi",
            header_timezone="UTC",
            user_language="vi",
        )
    ]
    assert repo.completed is not None
    assert "vài cân" in repo.completed["content"]
    assert events[-1].event == "message.completed"


@pytest.mark.asyncio
async def test_long_turn_renews_the_generation_lease():
    repo = _FakeRepo(claim=_claim())
    renewed = asyncio.Event()
    calls: list[dict] = []

    async def extend_generation_lease(**kwargs):
        calls.append(kwargs)
        renewed.set()
        return True

    repo.extend_generation_lease = extend_generation_lease

    class _WaitForRenewal:
        async def stream(self, **kwargs):
            del kwargs
            await asyncio.wait_for(renewed.wait(), timeout=1)
            yield ChatCompletionDelta(text="The answer is ready. ")

    orchestrator = _orchestrator(repo, completion=_WaitForRenewal())
    orchestrator._lease_renew_interval = lambda: 0.01  # type: ignore[method-assign]
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
    assert calls
    assert calls[0]["message_id"] == "m-asst"
    assert calls[0]["generation_id"] == "gen-1"
    assert calls[0]["lease_expires_at"] > utc_now()
    assert events[-1].event == "message.completed"
    assert repo.failed is None


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
    # Cook and ingredient questions for the featured card stay in front.
    # The third slot keeps the model's remaining-budget chip.
    assert completed.data["follow_ups"] == [
        {"label": "How do I cook Egg rice bowl?", "action": "ask"},
        {"label": "What's in Egg rice bowl?", "action": "ask"},
        {"label": "What's left?", "action": "remaining_budget"},
    ]
    assert repo.attached_follow_ups["follow_ups"] == completed.data["follow_ups"]
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
    assert completion.tools == []


@pytest.mark.asyncio
async def test_how_to_cook_ignores_a_new_meal_tool_call():
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
            "suggestions": [
                {
                    "name": "Tofu bowl",
                    "calories": 420,
                    "recipe_steps": [
                        {"step": 1, "instruction": "Press the tofu dry."},
                        {"step": 2, "instruction": "Pan-fry until golden."},
                    ],
                }
            ],
        },
    )
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=_three_cards(), meal_slot="lunch")
    )
    repo = _FakeRepo(claim=_claim(), history=[prior])
    completion = _FakeCompletion(
        ["Press the tofu dry, then pan-fry until golden. "],
        tool_calls=[{"id": "c1", "name": "suggest_next_meal", "args": {}}],
    )
    orchestrator = _orchestrator(repo, next_meals=next_meals, completion=completion)

    events = await _stream(orchestrator, content="How do I cook the tofu?")
    completed = next(event for event in events if event.event == "message.completed")

    assert next_meals.calls == []
    assert completion.stream_calls == 0
    assert completed.data.get("intent") != "next_meal"
    assert "Egg rice bowl" not in repo.completed["content"]
    assert repo.completed["content"] == (
        "How to make Tofu bowl:\n1. Press the tofu dry.\n2. Pan-fry until golden."
    )


@pytest.mark.asyncio
async def test_ingredient_question_uses_the_shown_card():
    prior = ChatMessage(
        id="m-prior",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.COMPLETED,
        created_at=utc_now(),
        updated_at=utc_now(),
        content="Try Vegetarian pad thai.",
        reply_payload={
            "intent": "next_meal",
            "suggestions": [
                {
                    "name": "Vegetarian pad thai",
                    "calories": 480,
                    "ingredients": [
                        {"name": "rice noodles", "amount": 80, "unit": "g"},
                        {"name": "tofu", "amount": 100, "unit": "g"},
                    ],
                }
            ],
        },
    )
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=_three_cards(), meal_slot="lunch")
    )
    repo = _FakeRepo(claim=_claim(), history=[prior])
    completion = _FakeCompletion(
        ["It contains rice noodles and tofu. "],
        tool_calls=[{"id": "c1", "name": "suggest_next_meal", "args": {}}],
    )
    orchestrator = _orchestrator(repo, next_meals=next_meals, completion=completion)

    events = await _stream(orchestrator, content="What's in Vegetarian pad thai?")
    completed = next(event for event in events if event.event == "message.completed")

    assert next_meals.calls == []
    assert completion.stream_calls == 0
    assert completed.data.get("intent") != "next_meal"
    assert "rice noodles" in repo.completed["content"]
    assert "Egg rice bowl" not in repo.completed["content"]
    assert completed.data["suggestions"][0]["name"] == "Vegetarian pad thai"


@pytest.mark.asyncio
async def test_invented_cook_steps_are_replaced_with_the_card():
    prior = ChatMessage(
        id="m-prior",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.COMPLETED,
        created_at=utc_now(),
        updated_at=utc_now(),
        content="Try the tofu bowl.",
        reply_payload={
            "suggestions": [
                {
                    "name": "Tofu bowl",
                    "recipe_steps": [
                        {"instruction": "Press the tofu dry."},
                        {"instruction": "Pan-fry until golden."},
                    ],
                }
            ]
        },
    )
    repo = _FakeRepo(claim=_claim(), history=[prior])
    completion = _FakeCompletion(["Bake it at 400 degrees for an hour. "])
    orchestrator = _orchestrator(repo, completion=completion)

    await _stream(orchestrator, content="How do I cook the tofu?")

    assert completion.stream_calls == 0
    assert "400" not in repo.completed["content"]
    assert "Press the tofu dry" in repo.completed["content"]
    assert "Pan-fry until golden" in repo.completed["content"]


@pytest.mark.asyncio
async def test_invented_ingredients_are_replaced_with_the_card():
    prior = ChatMessage(
        id="m-prior",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.COMPLETED,
        created_at=utc_now(),
        updated_at=utc_now(),
        content="Try the tofu bowl.",
        reply_payload={
            "suggestions": [
                {
                    "name": "Tofu bowl",
                    "ingredients": [
                        {"name": "firm tofu", "amount": 150, "unit": "g"},
                        {"name": "rice", "amount": 80, "unit": "g"},
                    ],
                }
            ]
        },
    )
    repo = _FakeRepo(claim=_claim(), history=[prior])
    completion = _FakeCompletion(["It is mostly chicken and 999 grams of cheese. "])
    orchestrator = _orchestrator(repo, completion=completion)

    await _stream(orchestrator, content="What's in the tofu?")

    assert completion.stream_calls == 0
    assert "chicken" not in repo.completed["content"]
    assert "999" not in repo.completed["content"]
    assert repo.completed["content"] == (
        "Tofu bowl uses:\n- firm tofu, 150 g\n- rice, 80 g"
    )


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
    """A next-meal chip keeps the template when the model does not name the card."""
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

    assert completion.stream_calls == 1
    assert next_meals.calls
    assert "Egg rice bowl" in repo.completed["content"]
    assert "hấp dẫn" not in repo.completed["content"]
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
    assert completion.stream_calls == 1
    assert "Egg rice bowl" in repo.completed["content"]
    assert "Yogurt cup and Tofu scramble" in repo.completed["content"]
    assert "Oatmeal fits" not in repo.completed["content"]


@pytest.mark.asyncio
async def test_empty_meal_catalog_replaces_the_picking_line():
    """No card means no invented dish. The picking line is replaced and the model is not called."""
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=[], meal_slot="lunch")
    )
    completion = _FakeCompletion(["Try a 900 kcal burger. "])
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, next_meals=next_meals, completion=completion)

    events = await _stream(orchestrator, intent="next_meal", content="Lunch ideas")
    deltas = [event for event in events if event.event == "message.delta"]
    completed = next(event for event in events if event.event == "message.completed")

    assert deltas[0].data["delta"].startswith("I'm picking")
    replaced = next(event for event in deltas if event.data.get("replace") is True)
    assert replaced.data["delta"] == (
        "I couldn't find a lunch that fits what's left. "
        "Try another meal, or tell me what to change."
    )
    assert repo.completed["content"] == replaced.data["delta"]
    assert "I'm picking" not in repo.completed["content"]
    assert "900" not in repo.completed["content"]
    assert "burger" not in repo.completed["content"].casefold()
    assert completion.stream_calls == 0
    assert completed.data["suggestions"] == []
    assert completed.data["intent"] == "next_meal"


@pytest.mark.asyncio
async def test_meal_reply_keeps_a_model_sentence_that_names_the_card():
    cards = _three_cards()
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=cards, meal_slot="lunch")
    )
    written = (
        "Egg rice bowl is the one I'd make for lunch. "
        "Yogurt cup and Tofu scramble are there if you want a change. "
        "Tap a card for the recipe, or log it from the card."
    )
    completion = _FakeCompletion([written])
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, next_meals=next_meals, completion=completion)

    events = await _stream(orchestrator, intent="next_meal", content="Lunch ideas")
    deltas = [event for event in events if event.event == "message.delta"]

    assert deltas[0].data["delta"].startswith("I'm picking")
    assert deltas[0].data.get("replace") is not True
    replaced = next(event for event in deltas if event.data.get("replace") is True)
    assert replaced.data["delta"].startswith("Egg rice bowl")
    assert "I'm picking" not in repo.completed["content"]
    assert repo.completed["content"] == written
    assert "fits lunch" not in repo.completed["content"]
    assert "420" not in repo.completed["content"]


@pytest.mark.asyncio
async def test_meal_reply_keeps_the_card_calorie_number():
    cards = _three_cards()
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=cards, meal_slot="lunch")
    )
    written = "Egg rice bowl is 420 kcal. Tap the card to log it."
    completion = _FakeCompletion([written])
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, next_meals=next_meals, completion=completion)

    await _stream(orchestrator, intent="next_meal", content="Lunch ideas")

    assert repo.completed["content"] == written
    assert "fits lunch" not in repo.completed["content"]


@pytest.mark.asyncio
async def test_usable_meal_reply_names_a_dish_logged_today():
    cards = _three_cards()
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=cards, meal_slot="lunch")
    )
    context = _FakeContext(
        recent_meals=(
            ChatMealSummary(
                meal_id="pho",
                local_date="2026-09-01",
                dish_name="Pho",
                meal_type="breakfast",
                calories=480,
                protein_g=25,
                carbs_g=60,
                fat_g=12,
                status="active",
            ),
        )
    )
    written = "Egg rice bowl is 420 kcal."
    completion = _FakeCompletion([written])
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(
        repo,
        next_meals=next_meals,
        completion=completion,
        context_builder=context,
    )

    await _stream(orchestrator, intent="next_meal", content="Lunch ideas")

    assert repo.completed["content"] == (
        "You already logged Pho today. Egg rice bowl is 420 kcal."
    )
    assert "480" not in repo.completed["content"]


@pytest.mark.asyncio
async def test_meal_reply_drops_a_sentence_that_invents_calories():
    cards = _three_cards()
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=cards, meal_slot="lunch")
    )
    completion = _FakeCompletion(["Egg rice bowl is about 999 kcal. "])
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, next_meals=next_meals, completion=completion)

    await _stream(orchestrator, intent="next_meal", content="Lunch ideas")

    content = repo.completed["content"]
    assert "Egg rice bowl fits lunch." in content
    assert "You still have 80g carbs open today." in content
    assert "999" not in content


@pytest.mark.asyncio
async def test_meal_intro_names_a_dish_logged_today():
    cards = _three_cards()
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=cards, meal_slot="lunch")
    )
    context = _FakeContext(
        recent_meals=(
            ChatMealSummary(
                meal_id="earlier",
                local_date="2026-09-01",
                dish_name="Pho",
                meal_type="breakfast",
                calories=480,
                protein_g=25,
                carbs_g=60,
                fat_g=12,
                status="active",
            ),
            ChatMealSummary(
                meal_id="offered",
                local_date="2026-09-01",
                dish_name="Egg rice bowl",
                meal_type="snack",
                calories=420,
                protein_g=18,
                carbs_g=40,
                fat_g=14,
                status="active",
            ),
        )
    )
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, next_meals=next_meals, context_builder=context)

    await _stream(orchestrator, intent="next_meal", content="Lunch ideas")

    content = repo.completed["content"]
    assert content.startswith("You already logged Pho today.")
    assert "Egg rice bowl" in content
    assert "480" not in content
    assert "420" not in content


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
async def test_completed_reply_includes_the_meal_photo():
    class _SlowImages:
        def __init__(self) -> None:
            self.release = asyncio.Event()
            self.started = asyncio.Event()

        async def fetch(self, **kwargs):
            del kwargs
            return NextMealCandidateResult(
                suggestions=_three_cards(), meal_slot="lunch"
            )

        async def attach_images(self, suggestions):
            self.started.set()
            await self.release.wait()
            return [
                {**card, "thumbnail_url": "https://cdn.example/meal.jpg"}
                for card in suggestions
            ]

    class _FollowUps:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def generate_follow_ups(self, **kwargs):
            del kwargs
            self.started.set()
            return [{"label": "Another idea", "prompt": "More snacks"}]

    meals = _SlowImages()
    follow_ups = _FollowUps()
    orchestrator = _orchestrator(
        _FakeRepo(claim=_claim()), next_meals=meals, follow_ups=follow_ups
    )
    events: list = []

    async def collect() -> None:
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="Lunch ideas",
            idempotency_key="key-1",
            locale="en",
            header_timezone="UTC",
            user_language="en",
            intent="next_meal",
        ):
            events.append(event)

    task = asyncio.create_task(collect())
    await asyncio.wait_for(meals.started.wait(), timeout=1)
    await asyncio.wait_for(follow_ups.started.wait(), timeout=1)
    await asyncio.sleep(0.05)
    assert not any(event.event == "message.completed" for event in events)
    meals.release.set()
    await asyncio.wait_for(task, timeout=1)
    completed = next(event for event in events if event.event == "message.completed")
    ready = next(event for event in events if event.event == "message.ready")
    assert completed.data["follow_ups"]
    assert (
        completed.data["suggestions"][0]["thumbnail_url"]
        == "https://cdn.example/meal.jpg"
    )
    assert (
        ready.data["suggestions"][0]["thumbnail_url"] == "https://cdn.example/meal.jpg"
    )
    completed_at = next(
        index
        for index, event in enumerate(events)
        if event.event == "message.completed"
    )
    assert not any(
        event.event == "message.suggestions" for event in events[completed_at + 1 :]
    )


@pytest.mark.asyncio
async def test_meal_cards_arrive_before_the_sentence_is_replaced():
    cards = _three_cards()
    next_meals = _FakeNextMeals(
        NextMealCandidateResult(suggestions=cards, meal_slot="lunch")
    )
    orchestrator = _orchestrator(_FakeRepo(claim=_claim()), next_meals=next_meals)

    events = await _stream(orchestrator, intent="next_meal", content="Lunch ideas")
    suggestion_at = next(
        index
        for index, event in enumerate(events)
        if event.event == "message.suggestions"
    )
    replace_at = next(
        index
        for index, event in enumerate(events)
        if event.event == "message.delta" and event.data.get("replace") is True
    )

    assert suggestion_at < replace_at
    assert events[suggestion_at].data["suggestions"][0]["name"] == "Egg rice bowl"
    assert "thumbnail_url" not in events[suggestion_at].data["suggestions"][0]


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
        elif event.event == "message.ready":
            assert meals.events == ["fetch", "images"]
            assert (
                event.data["suggestions"][0]["thumbnail_url"]
                == "https://cdn.example/meal.jpg"
            )
        events.append(event)
    completed = next(event for event in events if event.event == "message.completed")
    assert intro_seen
    assert meals.events == ["fetch", "images"]
    named = next(
        event
        for event in events
        if event.event == "message.delta"
        and event.data.get("replace") is True
        and "Egg rice bowl" in event.data.get("delta", "")
    )
    assert "Egg rice bowl" in named.data["delta"]
    assert "I'm picking" not in named.data["delta"]
    assert completed.data["suggestions"][0]["thumbnail_url"] == (
        "https://cdn.example/meal.jpg"
    )
    completed_at = next(
        index
        for index, event in enumerate(events)
        if event.event == "message.completed"
    )
    assert not any(
        event.event == "message.suggestions" for event in events[completed_at + 1 :]
    )


@pytest.mark.asyncio
async def test_meal_photos_overlap_the_sentence():
    """Photos start while the meal sentence is still streaming.

    message.completed still waits for the URLs, so the card the app keeps
    has thumbnail_url.
    """

    class _Hold:
        def __init__(self) -> None:
            self.photos_started = asyncio.Event()
            self.sentence_started = asyncio.Event()
            self.release_sentence = asyncio.Event()
            self.photos_done = asyncio.Event()
            self.photos_finished_during_sentence = False

    hold = _Hold()

    class _Meals:
        async def fetch(self, **kwargs):
            del kwargs
            return NextMealCandidateResult(
                suggestions=_three_cards(), meal_slot="snack"
            )

        async def attach_images(self, suggestions):
            hold.photos_started.set()
            await hold.sentence_started.wait()
            hold.photos_finished_during_sentence = not hold.release_sentence.is_set()
            hold.photos_done.set()
            return [
                {**card, "thumbnail_url": "https://cdn.example/meal.jpg"}
                for card in suggestions
            ]

    class _SlowSentence:
        async def stream(self, **kwargs):
            del kwargs
            hold.sentence_started.set()
            yield ChatCompletionDelta(text="Egg rice bowl fits this snack. ")
            await hold.release_sentence.wait()
            yield ChatCompletionDelta(
                text="",
                usage=ChatUsage(input_tokens=10, output_tokens=4, model="gpt-5.6-luna"),
                done=True,
            )

    orchestrator = _orchestrator(
        _FakeRepo(claim=_claim()),
        completion=_SlowSentence(),
        next_meals=_Meals(),
    )
    events: list = []

    async def collect() -> None:
        async for event in orchestrator.stream_turn(
            user_id="u1",
            content="Snack ideas",
            idempotency_key="key-1",
            locale="en",
            header_timezone="UTC",
            user_language="en",
            intent="next_meal",
        ):
            events.append(event)

    task = asyncio.create_task(collect())
    await asyncio.wait_for(hold.photos_started.wait(), timeout=2)
    await asyncio.wait_for(hold.sentence_started.wait(), timeout=2)
    await asyncio.wait_for(hold.photos_done.wait(), timeout=2)
    assert hold.photos_finished_during_sentence
    assert not any(event.event == "message.completed" for event in events)
    hold.release_sentence.set()
    await asyncio.wait_for(task, timeout=2)
    completed = next(event for event in events if event.event == "message.completed")
    ready = next(event for event in events if event.event == "message.ready")
    assert (
        completed.data["suggestions"][0]["thumbnail_url"]
        == "https://cdn.example/meal.jpg"
    )
    assert (
        ready.data["suggestions"][0]["thumbnail_url"] == "https://cdn.example/meal.jpg"
    )


@pytest.mark.asyncio
async def test_meal_photo_generation_releases_the_chat_slot():
    class _Meals:
        def __init__(self) -> None:
            self.slot_free = False

        async def fetch(self, **kwargs):
            del kwargs
            return NextMealCandidateResult(
                suggestions=_three_cards(), meal_slot="lunch"
            )

        async def attach_images(self, suggestions):
            await asyncio.wait_for(orchestrator._semaphore.acquire(), timeout=0.05)
            orchestrator._semaphore.release()
            self.slot_free = True
            return suggestions

    meals = _Meals()
    orchestrator = _orchestrator(_FakeRepo(claim=_claim()), next_meals=meals)
    orchestrator._semaphore = asyncio.Semaphore(1)
    await _stream(orchestrator, content="What should I eat next?")
    assert meals.slot_free


@pytest.mark.asyncio
async def test_disconnect_during_meal_photos_still_stores_them():
    class _Meals:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def fetch(self, **kwargs):
            del kwargs
            return NextMealCandidateResult(
                suggestions=_three_cards(), meal_slot="dinner"
            )

        async def attach_images(self, suggestions):
            self.started.set()
            await self.release.wait()
            return [
                {**card, "thumbnail_url": "https://cdn.example/meal.jpg"}
                for card in suggestions
            ]

    meals = _Meals()
    repo = _FakeRepo(claim=_claim())
    saved = asyncio.Event()
    completes = 0

    async def save_reply_suggestions(**kwargs):
        repo.saved_suggestions = kwargs
        saved.set()

    original_complete = repo.complete_assistant_message

    async def complete_assistant_message(**kwargs):
        nonlocal completes
        completes += 1
        return await original_complete(**kwargs)

    repo.save_reply_suggestions = save_reply_suggestions
    repo.complete_assistant_message = complete_assistant_message
    orchestrator = _orchestrator(repo, next_meals=meals)

    async def collect() -> None:
        async for _event in orchestrator.stream_turn(
            user_id="u1",
            content="What should I eat next?",
            idempotency_key="key-1",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        ):
            pass

    task = asyncio.create_task(collect())
    await asyncio.wait_for(meals.started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not saved.is_set()
    meals.release.set()
    await asyncio.wait_for(saved.wait(), timeout=1)
    assert completes == 1
    assert repo.failed is None
    assert repo.saved_suggestions["suggestions"][0]["thumbnail_url"] == (
        "https://cdn.example/meal.jpg"
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
async def test_stopping_a_turn_cancels_follow_up_generation():
    class _SlowFollowUps:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def generate_follow_ups(self, **kwargs):
            del kwargs
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    follow_ups = _SlowFollowUps()
    orchestrator = _orchestrator(
        _FakeRepo(claim=_claim()),
        completion=_FakeCompletion(["Here is a start. "]),
        follow_ups=follow_ups,
    )

    async def collect() -> None:
        async for _event in orchestrator.stream_turn(
            user_id="u1",
            content="What should I eat next?",
            idempotency_key="key-1",
            locale="en",
            header_timezone="UTC",
            user_language="en",
        ):
            pass

    task = asyncio.create_task(collect())
    await asyncio.wait_for(follow_ups.started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(follow_ups.cancelled.wait(), timeout=1)


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
    # No meal card came back, so the model's sentence is not stored.
    assert repo.completed["content"] == (
        "I couldn't find a breakfast that fits what's left. "
        "Try another meal, or tell me what to change."
    )
    assert "Another idea" not in repo.completed["content"]


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


def test_history_remembers_recipe_steps_from_the_card():
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
                    "name": "Tofu bowl",
                    "recipe_steps": [
                        {"instruction": "Press the tofu dry."},
                        "Pan-fry until golden.",
                        {"text": "Slice and serve."},
                        {"instruction": "This fourth step is dropped."},
                    ],
                }
            ]
        },
    )
    turns = _history_turns([message])
    assert (
        "Recipe steps: 1. Press the tofu dry. "
        "2. Pan-fry until golden. 3. Slice and serve."
    ) in turns[0].content
    assert "fourth step" not in turns[0].content


def test_history_omits_missing_meal_macros():
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
                {"name": "Salad", "calories": 180, "protein_g": 6},
            ]
        },
    )
    turns = _history_turns([message])
    assert "180 kcal" in turns[0].content
    assert "6g protein" in turns[0].content
    assert "None" not in turns[0].content
    assert "carbs" not in turns[0].content


def _listing(thread_id: str, title: str | None) -> ChatThreadListing:
    now = utc_now()
    return ChatThreadListing(
        thread=ChatThread(id=thread_id, user_id="u1", created_at=now, updated_at=now),
        title=title,
        preview="Try grilled fish.",
        preview_role=ChatMessageRole.ASSISTANT,
        last_message_at=now,
    )


@pytest.mark.asyncio
async def test_list_threads_pages_with_one_extra_row():
    repo = _FakeRepo(claim=_claim())
    repo.listings = [
        _listing("t3", "Dinner?"),
        _listing("t2", None),
        _listing("t1", "x"),
    ]
    orchestrator = _orchestrator(repo)

    stamp = utc_now()
    cursor = encode_thread_cursor(stamp, "t9")

    payload = await orchestrator.list_threads(user_id="u1", limit=2, cursor=cursor)

    assert repo.list_kwargs == {"user_id": "u1", "limit": 3, "before": (stamp, "t9")}
    assert payload["has_more"] is True
    second = repo.listings[1].thread
    assert decode_thread_cursor(payload["next_cursor"]) == (second.updated_at, "t2")
    assert [item["id"] for item in payload["threads"]] == ["t3", "t2"]
    assert payload["threads"][0]["title"] == "Dinner?"
    assert payload["threads"][0]["preview_role"] == "assistant"
    assert payload["threads"][1]["title"] is None


@pytest.mark.asyncio
async def test_get_thread_by_id_reads_that_thread():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo)

    payload = await orchestrator.get_thread(
        user_id="u1", limit=50, before=None, thread_id="t1"
    )

    assert payload["thread"]["id"] == "t1"
    assert repo.got_thread_id == "t1"


@pytest.mark.asyncio
async def test_get_thread_by_unknown_id_raises_not_found():
    orchestrator = _orchestrator(_FakeRepo(claim=_claim()))

    with pytest.raises(ChatThreadNotFoundError):
        await orchestrator.get_thread(
            user_id="u1", limit=50, before=None, thread_id="someone-else"
        )


@pytest.mark.asyncio
async def test_create_thread_returns_thread_summary():
    orchestrator = _orchestrator(_FakeRepo(claim=_claim()))

    payload = await orchestrator.create_thread("u1")

    assert payload["id"] == "t1"
    assert set(payload) == {"id", "created_at", "updated_at"}


@pytest.mark.asyncio
async def test_clear_thread_targets_the_given_thread():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo)

    payload = await orchestrator.clear_thread("u1", "t1")

    assert payload == {"thread_id": "t1", "cleared": True}
    assert repo.cleared_thread_id == "t1"


@pytest.mark.asyncio
async def test_prepare_turn_claims_in_the_given_thread():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo)

    prepared = await orchestrator.prepare_turn(
        user_id="u1",
        content="Hi",
        idempotency_key="key-1",
        locale="en",
        header_timezone=None,
        user_language="en",
        thread_id="t1",
    )
    orchestrator.release_slot(prepared)

    assert repo.claim_kwargs["thread_id"] == "t1"


@pytest.mark.asyncio
async def test_prepare_regeneration_scopes_reopen_to_thread():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo)

    with pytest.raises(ChatRegenerateNotAllowedError):
        await orchestrator.prepare_regeneration(
            user_id="u1",
            assistant_message_id="m-asst",
            content="",
            locale="en",
            header_timezone=None,
            user_language="en",
            thread_id="t1",
        )

    assert repo.reopened["thread_id"] == "t1"


@pytest.mark.asyncio
async def test_last_page_has_no_next_cursor():
    repo = _FakeRepo(claim=_claim())
    repo.listings = [_listing("t1", "x")]
    orchestrator = _orchestrator(repo)

    payload = await orchestrator.list_threads(user_id="u1", limit=5)

    assert payload["has_more"] is False
    assert payload["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_threads_rejects_a_cursor_it_did_not_issue():
    orchestrator = _orchestrator(_FakeRepo(claim=_claim()))

    for bad in ("t1", "!!!", encode_thread_cursor(utc_now(), "")):
        with pytest.raises(ChatInvalidCursorError):
            await orchestrator.list_threads(user_id="u1", limit=5, cursor=bad)


@pytest.mark.asyncio
async def test_regenerate_in_a_foreign_thread_is_not_found():
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo)

    with pytest.raises(ChatThreadNotFoundError):
        await orchestrator.prepare_regeneration(
            user_id="u1",
            assistant_message_id="m-asst",
            content="",
            locale="en",
            header_timezone=None,
            user_language="en",
            thread_id="someone-else",
        )

    assert repo.reopened is None


class _TrackedProviderStream:
    """Async iterator whose aclose is explicit. Garbage collection does not close it."""

    def __init__(
        self,
        owner: "_TrackedCompletion",
        index: int,
        deltas: list[ChatCompletionDelta],
    ):
        self.owner = owner
        self.index = index
        self._deltas = list(deltas)
        self.consumed = 0
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.consumed >= len(self._deltas):
            raise StopAsyncIteration
        delta = self._deltas[self.consumed]
        self.consumed += 1
        return delta

    async def aclose(self):
        self.closed = True
        self.owner.events.append(("close", self.index))


class _TrackedCompletion:
    def __init__(self, rounds: list[list[ChatCompletionDelta]]):
        self._rounds = rounds
        self.streams: list[_TrackedProviderStream] = []
        self.events: list[tuple[str, int]] = []

    def stream(self, **kwargs):
        del kwargs
        index = len(self.streams)
        self.events.append(("open", index))
        deltas = self._rounds[index] if index < len(self._rounds) else []
        tracked = _TrackedProviderStream(self, index, deltas)
        self.streams.append(tracked)
        return tracked


def _assert_previous_stream_closed(completion: _TrackedCompletion) -> None:
    opens = [index for kind, index in completion.events if kind == "open"]
    assert len(opens) >= 2
    for index in opens[1:]:
        previous = index - 1
        close_at = completion.events.index(("close", previous))
        open_at = completion.events.index(("open", index))
        assert close_at < open_at
    assert all(stream.closed for stream in completion.streams)


class _StatusRepo(_FakeRepo):
    def __init__(self, claim):
        super().__init__(claim=claim)
        self.status = claim.assistant_message.status

    async def complete_assistant_message(self, **kwargs):
        message = await super().complete_assistant_message(**kwargs)
        self.status = ChatMessageStatus.COMPLETED
        return message

    async def fail_assistant_message(self, **kwargs):
        message = await super().fail_assistant_message(**kwargs)
        self.status = ChatMessageStatus.FAILED
        return message


@pytest.mark.asyncio
async def test_blocked_chunk_closes_the_provider_stream():
    completion = _TrackedCompletion(
        [
            [
                ChatCompletionDelta(text="Try a peanut snack. "),
                ChatCompletionDelta(
                    text="And then more. ",
                    done=True,
                    usage=ChatUsage(
                        input_tokens=4, output_tokens=8, model="gpt-5.6-luna"
                    ),
                ),
            ]
        ]
    )
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, completion=completion)

    await _stream(orchestrator, content="Cite protein guidance")

    assert len(completion.streams) == 1
    stream = completion.streams[0]
    assert stream.closed
    assert stream.consumed == 1
    assert repo.completed is not None
    assert "peanut" not in repo.completed["content"].casefold()


@pytest.mark.asyncio
async def test_tool_call_and_output_limit_retry_close_the_previous_stream():
    tool_completion = _TrackedCompletion(
        [
            [
                ChatCompletionDelta(
                    text="",
                    done=True,
                    tool_calls=[
                        {
                            "id": "c1",
                            "name": "check_daily_progress",
                            "args": {"focus": "remaining_budget"},
                        }
                    ],
                    usage=ChatUsage(
                        input_tokens=6, output_tokens=4, model="gpt-5.6-luna"
                    ),
                )
            ],
            [
                ChatCompletionDelta(text="Protein stays at your Nutree target. "),
                ChatCompletionDelta(
                    text="",
                    done=True,
                    usage=ChatUsage(
                        input_tokens=8, output_tokens=6, model="gpt-5.6-luna"
                    ),
                ),
            ],
        ]
    )
    tool_repo = _FakeRepo(claim=_claim())
    tool_orchestrator = _orchestrator(tool_repo, completion=tool_completion)
    await _stream(tool_orchestrator, content="Cite protein guidance")
    _assert_previous_stream_closed(tool_completion)
    assert tool_repo.completed is not None

    retry_completion = _TrackedCompletion(
        [
            [
                ChatCompletionDelta(text="Nếu"),
                ChatCompletionDelta(
                    text="",
                    done=True,
                    truncated=True,
                    usage=ChatUsage(
                        input_tokens=10, output_tokens=900, model="gpt-5.6-luna"
                    ),
                ),
            ],
            [
                ChatCompletionDelta(
                    text="Dư 200 kcal trong bốn tháng là khoảng vài cân. "
                ),
                ChatCompletionDelta(
                    text="",
                    done=True,
                    usage=ChatUsage(
                        input_tokens=8, output_tokens=30, model="gpt-5.6-luna"
                    ),
                ),
            ],
        ]
    )
    retry_repo = _FakeRepo(claim=_claim())
    retry_orchestrator = _orchestrator(retry_repo, completion=retry_completion)
    events = [
        event
        async for event in retry_orchestrator.stream_turn(
            user_id="u1",
            content="Ăn ngày dư 200 trong 4 tháng lên mấy cân",
            idempotency_key="key-1",
            locale="vi",
            header_timezone="UTC",
            user_language="vi",
        )
    ]
    _assert_previous_stream_closed(retry_completion)
    assert events[-1].event == "message.completed"


@pytest.mark.asyncio
async def test_outer_aclose_leaves_the_message_terminal():
    repo = _StatusRepo(_claim())
    orchestrator = _orchestrator(repo)
    prepared = PreparedChatTurn(
        claim=repo.claim,
        content="How much is left?",
        locale="en",
        header_timezone="UTC",
        started=0.0,
    )
    stream = orchestrator.stream_prepared(user_id="u1", prepared=prepared)
    started = await anext(stream)
    assert started.event == "message.started"
    assert repo.status == ChatMessageStatus.GENERATING

    before = set(asyncio.all_tasks())
    await stream.aclose()
    spawned = [
        task for task in asyncio.all_tasks() if task not in before and not task.done()
    ]
    if spawned:
        await asyncio.gather(*spawned)

    assert repo.status != ChatMessageStatus.GENERATING
    assert repo.status == ChatMessageStatus.FAILED
    assert repo.failed["error_code"] == "CHAT_CANCELLED"
    assert repo.completed is None


def _prepared_turn(claim, *, content: str, locale: str = "en") -> PreparedChatTurn:
    return PreparedChatTurn(
        claim=claim,
        content=content,
        locale=locale,
        header_timezone="UTC",
        started=0.0,
    )


async def _aclose_and_drain(stream) -> None:
    before = set(asyncio.all_tasks())
    await stream.aclose()
    spawned = [
        task for task in asyncio.all_tasks() if task not in before and not task.done()
    ]
    if spawned:
        await asyncio.gather(*spawned)


@pytest.mark.asyncio
async def test_outer_close_during_tool_call_loop_acloses_provider_stream():
    completion = _TrackedCompletion(
        [
            [
                ChatCompletionDelta(text="Protein stays at your Nutree target. "),
                ChatCompletionDelta(
                    text="",
                    done=True,
                    tool_calls=[
                        {
                            "id": "c1",
                            "name": "check_daily_progress",
                            "args": {"focus": "remaining_budget"},
                        }
                    ],
                    usage=ChatUsage(
                        input_tokens=6, output_tokens=4, model="gpt-5.6-luna"
                    ),
                ),
            ]
        ]
    )
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, completion=completion)
    stream = orchestrator.stream_prepared(
        user_id="u1",
        prepared=_prepared_turn(repo.claim, content="Cite protein guidance"),
    )
    async for event in stream:
        if event.event == "message.delta":
            break
    # Do not gc.collect(): aclose must run before the generator is collected.
    await _aclose_and_drain(stream)

    assert len(completion.streams) == 1
    assert completion.streams[0].closed
    assert completion.streams[0].consumed == 1


@pytest.mark.asyncio
async def test_outer_close_during_next_meal_stream_acloses_provider_stream():
    cards = _three_cards()
    completion = _TrackedCompletion(
        [
            [
                ChatCompletionDelta(text="Egg rice bowl fits the rest of your day. "),
                ChatCompletionDelta(
                    text="Tap the card to see the recipe. ",
                    done=True,
                    usage=ChatUsage(
                        input_tokens=8, output_tokens=12, model="gpt-5.6-luna"
                    ),
                ),
            ]
        ]
    )
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(
        repo,
        completion=completion,
        next_meals=_FakeNextMeals(
            NextMealCandidateResult(
                suggestions=cards, session_id="sess-9", meal_slot="breakfast"
            )
        ),
    )
    prepared = _prepared_turn(repo.claim, content="What should I eat next?")
    prepared.intent = "next_meal"
    stream = orchestrator.stream_prepared(user_id="u1", prepared=prepared)
    saw_model_delta = False
    async for event in stream:
        if event.event == "message.delta" and event.data.get("replace"):
            saw_model_delta = True
            break
    assert saw_model_delta
    # Assert before draining other tasks. The sentence generator's finalizer
    # would close the provider on a later tick, which hides a missing aclosing.
    # Do not gc.collect().
    await stream.aclose()
    assert len(completion.streams) == 1
    assert completion.streams[0].closed
    assert completion.streams[0].consumed == 1
    current = asyncio.current_task()
    spawned = [
        task for task in asyncio.all_tasks() if task is not current and not task.done()
    ]
    if spawned:
        await asyncio.gather(*spawned)


@pytest.mark.asyncio
async def test_outer_close_during_retry_loop_acloses_provider_stream():
    completion = _TrackedCompletion(
        [
            [
                ChatCompletionDelta(text="Nếu"),
                ChatCompletionDelta(
                    text="",
                    done=True,
                    truncated=True,
                    usage=ChatUsage(
                        input_tokens=10, output_tokens=900, model="gpt-5.6-luna"
                    ),
                ),
            ],
            [
                ChatCompletionDelta(
                    text="Dư 200 kcal trong bốn tháng là khoảng vài cân. "
                ),
                ChatCompletionDelta(
                    text="Phần còn lại. ",
                    done=True,
                    usage=ChatUsage(
                        input_tokens=8, output_tokens=30, model="gpt-5.6-luna"
                    ),
                ),
            ],
        ]
    )
    repo = _FakeRepo(claim=_claim())
    orchestrator = _orchestrator(repo, completion=completion)
    stream = orchestrator.stream_prepared(
        user_id="u1",
        prepared=_prepared_turn(
            repo.claim,
            content="Ăn ngày dư 200 trong 4 tháng lên mấy cân",
            locale="vi",
        ),
    )
    saw_retry = False
    async for event in stream:
        if event.event == "message.delta" and event.data.get("replace"):
            saw_retry = True
            break
    assert saw_retry
    # Do not gc.collect(): the in-flight retry stream must be closed by aclose.
    await _aclose_and_drain(stream)

    assert len(completion.streams) == 2
    assert completion.streams[0].closed
    assert completion.streams[1].closed
    assert completion.streams[1].consumed == 1


class _GenerationGuardedRepo(_FakeRepo):
    """Match the repository fence: a stale generation_id updates nothing."""

    def __init__(self, claim, live_generation_id: str):
        super().__init__(claim=claim)
        self.live_generation_id = live_generation_id
        self.live_status = ChatMessageStatus.GENERATING
        self.fail_calls: list[dict] = []
        self.complete_calls: list[dict] = []

    def _owns(self, generation_id: str | None) -> bool:
        if self.live_status != ChatMessageStatus.GENERATING:
            return False
        # A missing generation_id does not fence the update, same as the repo.
        if generation_id is not None and generation_id != self.live_generation_id:
            return False
        return True

    async def complete_assistant_message(self, **kwargs):
        self.complete_calls.append(kwargs)
        self.completed = kwargs
        if not self._owns(kwargs.get("generation_id")):
            return None
        self.live_status = ChatMessageStatus.COMPLETED
        return await super().complete_assistant_message(**kwargs)

    async def fail_assistant_message(self, **kwargs):
        self.fail_calls.append(kwargs)
        if not self._owns(kwargs.get("generation_id")):
            return None
        self.failed = kwargs
        self.live_status = ChatMessageStatus.FAILED
        return self.claim.assistant_message


@pytest.mark.asyncio
async def test_stale_generation_disconnect_does_not_apply_chat_cancelled():
    repo = _GenerationGuardedRepo(_claim(), live_generation_id="gen-2")
    orchestrator = _orchestrator(repo, completion=_FakeCompletion(["about 650 "]))
    stream = orchestrator.stream_prepared(
        user_id="u1",
        prepared=_prepared_turn(repo.claim, content="Cite protein guidance"),
    )
    saw_stale_error = False
    async for event in stream:
        if event.event == "message.error":
            assert event.data["code"] == "CHAT_TURN_FAILED"
            saw_stale_error = True
            break
    assert saw_stale_error
    writes_before_close = len(repo.complete_calls) + len(repo.fail_calls)

    await _aclose_and_drain(stream)

    assert len(repo.complete_calls) + len(repo.fail_calls) > writes_before_close
    assert repo.fail_calls
    assert all(call["generation_id"] == "gen-1" for call in repo.fail_calls)
    assert all(call["error_code"] == "CHAT_CANCELLED" for call in repo.fail_calls)
    assert repo.failed is None
    assert repo.live_status == ChatMessageStatus.GENERATING
    assert repo.live_generation_id == "gen-2"
