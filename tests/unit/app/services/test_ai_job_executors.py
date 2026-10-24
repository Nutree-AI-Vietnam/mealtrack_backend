"""Executors run one attempt of each background AI job type."""

import asyncio
import logging
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, create_autospec
from uuid import UUID

import pytest
from tests.fixtures.fakes.fake_ai_jobs import USER_ID, FakeAiJobsUow, make_claim

from src.app.commands.meal.parse_meal_text_command import ParseMealTextCommand
from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.services.ai_jobs.executors import (
    AiJobInterrupt,
    ChatJobExecutor,
    MealScanJobExecutor,
    ParseTextJobExecutor,
    chat_job_result,
    drain_chat_turn,
    run_chat_turn,
)
from src.domain.exceptions.ai_job_exceptions import AiJobExecutionError
from src.domain.model.ai_jobs import AiJobType
from src.domain.model.chat.models import ChatSseEvent
from src.domain.model.meal import MealStatus
from src.domain.model.meal_projection import MealProjection
from src.domain.ports.meal_repository_port import MealRepositoryPort

SUPERVISION_LOGGER = "src.app.services.ai_jobs.supervision"
MEAL_ID = "8d0c6c1e-2f43-4b8e-9a7e-5c1d2b3a4f60"
IMAGE_URL = "https://res.cloudinary.com/demo/image/upload/v1/mealtrack/img-1.jpg"

STARTED = ChatSseEvent(
    event="message.started",
    data={
        "thread_id": "thread-1",
        "user_message_id": "msg-1",
        "assistant_message_id": "msg-2",
    },
)
DELTA = ChatSseEvent(event="message.delta", data={"text": "Thử "})
COMPLETED = ChatSseEvent(
    event="message.completed",
    data={
        "thread_id": "thread-1",
        "assistant_message_id": "msg-2",
        "model": "test-model",
    },
)
ERROR = ChatSseEvent(
    event="message.error",
    data={
        "code": "CHAT_PROVIDER_UNAVAILABLE",
        "retryable": True,
        "retry_after": 30,
        "assistant_message_id": "msg-2",
    },
)


def _scan_payload(**overrides):
    return {
        "meal_id": MEAL_ID,
        "image_url": IMAGE_URL,
        "public_id": "mealtrack/img-1",
        "user_description": "bún chả",
        "target_date": "2026-10-12",
        "scan_mode": "scanner",
        "label_crop_image_url": None,
        "label_crop_public_id": None,
        "crop_metadata": None,
        **overrides,
    }


def _scan_claim(*, drop=(), **overrides):
    payload = {
        key: value
        for key, value in _scan_payload(**overrides).items()
        if key not in drop
    }
    return make_claim(
        job_type=AiJobType.MEAL_SCAN, language="vi", request_payload=payload
    )


def _meal(status, *, user_id=USER_ID, meal_id=MEAL_ID):
    return SimpleNamespace(meal_id=meal_id, user_id=user_id, status=status)


async def _scan(uow, send, claim):
    executor = MealScanJobExecutor(send=send, uow_factory=uow)
    return await executor.execute(claim, AiJobInterrupt())


@pytest.fixture
def uow():
    uow = FakeAiJobsUow()
    uow.meals = create_autospec(MealRepositoryPort, instance=True)
    uow.meals.find_by_id.return_value = None
    return uow


@pytest.fixture
def send():
    return AsyncMock(return_value=_meal(MealStatus.READY))


class FakeOrchestrator:
    """Streams scripted chat events; can block mid-stream like a slow model."""

    def __init__(
        self, events=(), *, block=False, abandon_error=None, abandon_gate=None
    ):
        self._events = list(events)
        self._block = block
        self._abandon_error = abandon_error
        self._abandon_gate = abandon_gate
        self.prepared = SimpleNamespace(name="prepared-turn")
        self.prepare_kwargs = None
        self.stream_calls = []
        self.abandoned = []
        self.stream_closed = False
        self.streaming = asyncio.Event()
        self.abandon_started = asyncio.Event()
        self.abandon_done = asyncio.Event()

    async def prepare_turn(self, **kwargs):
        self.prepare_kwargs = kwargs
        return self.prepared

    async def stream_prepared(self, *, user_id, prepared):
        self.stream_calls.append((user_id, prepared))
        try:
            for event in self._events:
                yield event
            if self._block:
                self.streaming.set()
                await asyncio.Event().wait()
        finally:
            self.stream_closed = True

    async def abandon_turn(self, prepared, *, error_code):
        self.abandon_started.set()
        try:
            if self._abandon_gate is not None:
                await self._abandon_gate.wait()
            self.abandoned.append((prepared, error_code))
            if self._abandon_error is not None:
                raise self._abandon_error
        finally:
            self.abandon_done.set()


class _PlainEvents:
    """An async iterator without ``aclose``."""

    def __init__(self, *events):
        self._events = list(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


def _start_turn(orchestrator, interrupt):
    return asyncio.create_task(
        run_chat_turn(
            orchestrator,
            user_id=USER_ID,
            prepared=orchestrator.prepared,
            interrupt=interrupt,
        )
    )


# --- AiJobInterrupt ---


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (None, "CHAT_TURN_FAILED"),
        ("CHAT_STOPPED", "CHAT_STOPPED"),
        ("WORKER_INTERRUPTED", "CHAT_TURN_FAILED"),
        ("AI_TIMEOUT", "CHAT_TURN_FAILED"),
    ],
)
def test_interrupted_reply_is_stopped_only_when_the_user_stopped_it(code, expected):
    assert AiJobInterrupt(code).chat_error_code == expected


# --- MealScanJobExecutor ---


@pytest.mark.asyncio
async def test_meal_scan_analyzes_the_photo_under_the_job_meal_id(uow, send):
    transactions_at_send = []

    async def scan(command):
        transactions_at_send.append((uow.opened, uow.committed))
        return _meal(MealStatus.READY)

    send.side_effect = scan

    result = await _scan(uow, send, _scan_claim())

    assert result == {"meal_id": MEAL_ID}
    uow.meals.find_by_id.assert_awaited_once_with(
        MEAL_ID, projection=MealProjection.MACROS_ONLY
    )
    send.assert_awaited_once_with(
        ScanByUrlCommand(
            user_id=USER_ID,
            image_url=IMAGE_URL,
            public_id="mealtrack/img-1",
            user_description="bún chả",
            target_date=date(2026, 10, 12),
            language="vi",
            scan_mode="scanner",
            label_crop_image_url=None,
            label_crop_public_id=None,
            crop_metadata=None,
            meal_id=MEAL_ID,
        )
    )
    # The lookup's session is closed before the slow analysis starts.
    assert transactions_at_send == [(1, 1)]


@pytest.mark.asyncio
async def test_meal_scan_passes_the_food_label_crop_through(uow, send):
    crop_url = "https://res.cloudinary.com/demo/image/upload/v1/mealtrack/crop-1.jpg"
    crop_metadata = {"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.4}
    claim = _scan_claim(
        scan_mode="food_label",
        user_description=None,
        label_crop_image_url=crop_url,
        label_crop_public_id="mealtrack/crop-1",
        crop_metadata=crop_metadata,
    )

    await _scan(uow, send, claim)

    command = send.await_args.args[0]
    assert command.scan_mode == "food_label"
    assert command.user_description is None
    assert command.label_crop_image_url == crop_url
    assert command.label_crop_public_id == "mealtrack/crop-1"
    assert command.crop_metadata == crop_metadata


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [{"drop": ("scan_mode",)}, {"scan_mode": None}, {"scan_mode": ""}],
    ids=["missing", "none", "blank"],
)
async def test_meal_scan_defaults_to_the_scanner_mode(uow, send, overrides):
    await _scan(uow, send, _scan_claim(**overrides))

    assert send.await_args.args[0].scan_mode == "scanner"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [MealStatus.READY, MealStatus.INACTIVE])
async def test_meal_scan_skips_a_meal_an_earlier_attempt_finished(uow, send, status):
    uow.meals.find_by_id.return_value = _meal(status)

    result = await _scan(uow, send, _scan_claim())

    assert result == {"meal_id": MEAL_ID}
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        MealStatus.PROCESSING,
        MealStatus.ANALYZING,
        MealStatus.ENRICHING,
        MealStatus.FAILED,
    ],
)
async def test_meal_scan_reanalyzes_a_meal_an_earlier_attempt_left_unfinished(
    uow, send, status
):
    uow.meals.find_by_id.return_value = _meal(status)

    result = await _scan(uow, send, _scan_claim())

    assert result == {"meal_id": MEAL_ID}
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_meal_scan_refuses_a_meal_id_owned_by_another_user(uow, send):
    uow.meals.find_by_id.return_value = _meal(MealStatus.READY, user_id="u2")

    with pytest.raises(AiJobExecutionError) as exc_info:
        await _scan(uow, send, _scan_claim())

    assert exc_info.value.code == "INVALID_JOB_INPUT"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_meal_scan_fails_when_the_analysis_fails_the_meal(uow, send):
    send.return_value = _meal(MealStatus.FAILED)

    with pytest.raises(AiJobExecutionError) as exc_info:
        await _scan(uow, send, _scan_claim())

    assert exc_info.value.code == "FAILED_TO_ANALYZE_MEAL_IMAGE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returned", "expected"),
    [
        (None, MEAL_ID),
        (SimpleNamespace(status=MealStatus.READY), MEAL_ID),
        (_meal(MealStatus.READY, meal_id=None), MEAL_ID),
        (_meal(MealStatus.READY, meal_id="saved-id"), "saved-id"),
        (_meal(MealStatus.READY, meal_id=UUID(MEAL_ID)), MEAL_ID),
    ],
    ids=["nothing", "no-id-field", "no-id", "saved-id", "uuid-id"],
)
async def test_meal_scan_reports_the_saved_meal_id(uow, send, returned, expected):
    send.return_value = returned

    result = await _scan(uow, send, _scan_claim())

    assert result == {"meal_id": expected}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [{"drop": ("meal_id",)}, {"meal_id": "  "}, {"meal_id": 123}],
    ids=["missing", "blank", "not-a-string"],
)
async def test_meal_scan_without_a_meal_id_is_invalid_input(uow, send, overrides):
    with pytest.raises(AiJobExecutionError) as exc_info:
        await _scan(uow, send, _scan_claim(**overrides))

    assert exc_info.value.code == "INVALID_JOB_INPUT"
    assert uow.opened == 0
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"image_url": None},
        {"public_id": ""},
        {"user_description": 5},
        {"target_date": "12/10/2026"},
        {"target_date": 20261012},
        {"scan_mode": 1},
        {"crop_metadata": "x"},
        {"label_crop_image_url": 7},
    ],
    ids=[
        "no-image-url",
        "blank-public-id",
        "description-not-a-string",
        "date-not-iso",
        "date-not-a-string",
        "mode-not-a-string",
        "crop-metadata-not-a-dict",
        "crop-url-not-a-string",
    ],
)
async def test_meal_scan_with_malformed_input_is_invalid_input(uow, send, overrides):
    with pytest.raises(AiJobExecutionError) as exc_info:
        await _scan(uow, send, _scan_claim(**overrides))

    assert exc_info.value.code == "INVALID_JOB_INPUT"
    send.assert_not_awaited()


# --- ParseTextJobExecutor ---


@pytest.mark.asyncio
async def test_parse_text_sends_the_description_and_maps_the_response():
    response = SimpleNamespace(items=["egg"])
    send = AsyncMock(return_value=response)
    to_result = Mock(return_value={"items": [{"name": "egg"}]})
    claim = make_claim(
        language="vi",
        request_payload={"text": "2 trứng", "current_items": [{"name": "egg"}]},
    )

    result = await ParseTextJobExecutor(send=send, to_result=to_result).execute(
        claim, AiJobInterrupt()
    )

    assert result == {"items": [{"name": "egg"}]}
    send.assert_awaited_once_with(
        ParseMealTextCommand(
            text="2 trứng",
            language="vi",
            user_id=USER_ID,
            current_items=[{"name": "egg"}],
        )
    )
    to_result.assert_called_once_with(response)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [{"text": "2 eggs"}, {"text": "2 eggs", "current_items": None}],
    ids=["missing", "none"],
)
async def test_parse_text_without_current_items_starts_from_scratch(payload):
    send = AsyncMock(return_value=SimpleNamespace())

    await ParseTextJobExecutor(send=send, to_result=Mock(return_value={})).execute(
        make_claim(request_payload=payload), AiJobInterrupt()
    )

    assert send.await_args.args[0].current_items is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current_items",
    [{"name": "egg"}, "egg", 3],
    ids=["dict", "string", "number"],
)
async def test_parse_text_with_malformed_current_items_is_invalid_input(
    current_items,
):
    send = AsyncMock()
    to_result = Mock()
    claim = make_claim(
        request_payload={"text": "2 eggs", "current_items": current_items}
    )

    with pytest.raises(AiJobExecutionError) as exc_info:
        await ParseTextJobExecutor(send=send, to_result=to_result).execute(
            claim, AiJobInterrupt()
        )

    assert exc_info.value.code == "INVALID_JOB_INPUT"
    send.assert_not_awaited()
    to_result.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [{}, {"text": ""}, {"text": "   "}, {"text": 5}],
    ids=["missing", "empty", "blank", "not-a-string"],
)
async def test_parse_text_without_text_is_invalid_input(payload):
    send = AsyncMock()

    with pytest.raises(AiJobExecutionError) as exc_info:
        await ParseTextJobExecutor(send=send, to_result=Mock()).execute(
            make_claim(request_payload=payload), AiJobInterrupt()
        )

    assert exc_info.value.code == "INVALID_JOB_INPUT"
    send.assert_not_awaited()


# --- ChatJobExecutor ---

FULL_CHAT_PAYLOAD = {
    "content": "Bữa tối nay nên ăn gì?",
    "idempotency_key": "turn-key-1",
    "locale": "vi",
    "header_timezone": "Asia/Ho_Chi_Minh",
    "user_language": "vi",
    "intent": "meal_ideas",
    "function_name": "suggest_meals",
    "function_args": {"meal_type": "dinner"},
}


def _chat_claim(payload):
    return make_claim(job_type=AiJobType.CHAT, language="vi", request_payload=payload)


async def _chat(orchestrator, payload, interrupt=None):
    executor = ChatJobExecutor(orchestrator_factory=lambda: orchestrator)
    return await executor.execute(_chat_claim(payload), interrupt or AiJobInterrupt())


@pytest.mark.asyncio
async def test_chat_reruns_the_turn_and_returns_the_reply_ids():
    orchestrator = FakeOrchestrator([STARTED, DELTA, COMPLETED])

    result = await _chat(orchestrator, FULL_CHAT_PAYLOAD)

    assert result == {"thread_id": "thread-1", "assistant_message_id": "msg-2"}
    assert orchestrator.prepare_kwargs == {"user_id": USER_ID, **FULL_CHAT_PAYLOAD}


@pytest.mark.asyncio
async def test_chat_reruns_the_turn_on_the_thread_it_was_sent_to():
    orchestrator = FakeOrchestrator([COMPLETED])

    await _chat(orchestrator, {**FULL_CHAT_PAYLOAD, "thread_id": "thread-9"})

    assert orchestrator.prepare_kwargs == {
        "user_id": USER_ID,
        **FULL_CHAT_PAYLOAD,
        "thread_id": "thread-9",
    }
    assert orchestrator.stream_calls == [(USER_ID, orchestrator.prepared)]
    assert orchestrator.stream_closed
    assert orchestrator.abandoned == []


@pytest.mark.asyncio
async def test_chat_leaves_missing_optional_fields_empty():
    orchestrator = FakeOrchestrator([COMPLETED])

    await _chat(orchestrator, {"content": "Hi", "idempotency_key": "turn-key-1"})

    assert orchestrator.prepare_kwargs == {
        "user_id": USER_ID,
        "content": "Hi",
        "idempotency_key": "turn-key-1",
        "locale": None,
        "header_timezone": None,
        "user_language": None,
        "intent": None,
        "function_name": None,
        "function_args": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "function_args", [["dinner"], "dinner", 1], ids=["list", "string", "number"]
)
async def test_chat_with_malformed_function_args_is_invalid_input(function_args):
    factory = Mock()
    executor = ChatJobExecutor(orchestrator_factory=factory)
    claim = _chat_claim({**FULL_CHAT_PAYLOAD, "function_args": function_args})

    with pytest.raises(AiJobExecutionError) as exc_info:
        await executor.execute(claim, AiJobInterrupt())

    assert exc_info.value.code == "INVALID_JOB_INPUT"
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"idempotency_key": "turn-key-1"},
        {"content": "Hi"},
        {"content": "  ", "idempotency_key": "turn-key-1"},
        {"content": "Hi", "idempotency_key": "turn-key-1", "locale": 5},
    ],
    ids=["no-content", "no-idempotency-key", "blank-content", "locale-not-a-string"],
)
async def test_chat_with_malformed_input_is_invalid_input(payload):
    orchestrator = FakeOrchestrator([COMPLETED])

    with pytest.raises(AiJobExecutionError) as exc_info:
        await _chat(orchestrator, payload)

    assert exc_info.value.code == "INVALID_JOB_INPUT"
    assert orchestrator.prepare_kwargs is None


@pytest.mark.asyncio
async def test_chat_reply_error_fails_the_attempt_with_its_code():
    orchestrator = FakeOrchestrator([STARTED, ERROR])

    with pytest.raises(AiJobExecutionError) as exc_info:
        await _chat(orchestrator, FULL_CHAT_PAYLOAD)

    assert exc_info.value.code == "CHAT_PROVIDER_UNAVAILABLE"
    assert orchestrator.stream_closed


@pytest.mark.asyncio
async def test_chat_stream_that_ends_early_fails_the_attempt():
    orchestrator = FakeOrchestrator([STARTED, DELTA])

    with pytest.raises(AiJobExecutionError) as exc_info:
        await _chat(orchestrator, FULL_CHAT_PAYLOAD)

    assert exc_info.value.code == "CHAT_TURN_FAILED"


@pytest.mark.asyncio
async def test_cancelled_chat_attempt_fails_the_reply_with_the_stop_reason():
    orchestrator = FakeOrchestrator([STARTED], block=True)
    interrupt = AiJobInterrupt()
    task = asyncio.create_task(_chat(orchestrator, FULL_CHAT_PAYLOAD, interrupt))
    await asyncio.wait_for(orchestrator.streaming.wait(), 1)

    interrupt.code = "CHAT_STOPPED"
    task.cancel()
    await asyncio.wait({task}, timeout=1)

    assert task.cancelled()
    assert orchestrator.abandoned == [(orchestrator.prepared, "CHAT_STOPPED")]


# --- run_chat_turn ---


@pytest.mark.asyncio
async def test_run_chat_turn_returns_the_terminal_event_and_forwards_events():
    orchestrator = FakeOrchestrator([STARTED, DELTA, COMPLETED])
    seen = []

    terminal = await run_chat_turn(
        orchestrator,
        user_id=USER_ID,
        prepared=orchestrator.prepared,
        interrupt=AiJobInterrupt(),
        on_event=seen.append,
    )

    assert terminal == COMPLETED
    assert seen == [STARTED, DELTA, COMPLETED]
    assert orchestrator.abandoned == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (None, "CHAT_TURN_FAILED"),
        ("CHAT_STOPPED", "CHAT_STOPPED"),
        ("WORKER_INTERRUPTED", "CHAT_TURN_FAILED"),
    ],
)
async def test_cancelled_turn_is_abandoned_with_the_interrupt_reason(code, expected):
    orchestrator = FakeOrchestrator([STARTED], block=True)
    interrupt = AiJobInterrupt()
    task = _start_turn(orchestrator, interrupt)
    await asyncio.wait_for(orchestrator.streaming.wait(), 1)

    interrupt.code = code
    task.cancel()
    await asyncio.wait({task}, timeout=1)

    assert task.cancelled()
    assert orchestrator.abandoned == [(orchestrator.prepared, expected)]
    assert orchestrator.stream_closed


@pytest.mark.asyncio
async def test_failed_abandon_is_logged_and_the_cancel_still_wins(caplog):
    orchestrator = FakeOrchestrator(
        [STARTED], block=True, abandon_error=RuntimeError("db down")
    )
    task = _start_turn(orchestrator, AiJobInterrupt())
    await asyncio.wait_for(orchestrator.streaming.wait(), 1)

    with caplog.at_level(logging.WARNING, logger=SUPERVISION_LOGGER):
        task.cancel()
        await asyncio.wait({task}, timeout=1)

    assert task.cancelled()
    assert [r.getMessage() for r in caplog.records if r.name == SUPERVISION_LOGGER] == [
        "AI job cleanup failed: abandon chat turn"
    ]


@pytest.mark.asyncio
async def test_second_cancel_stops_waiting_but_the_abandon_still_lands():
    gate = asyncio.Event()
    orchestrator = FakeOrchestrator([STARTED], block=True, abandon_gate=gate)
    task = _start_turn(orchestrator, AiJobInterrupt())
    await asyncio.wait_for(orchestrator.streaming.wait(), 1)

    task.cancel()
    await asyncio.wait_for(orchestrator.abandon_started.wait(), 1)
    task.cancel()
    await asyncio.wait({task}, timeout=1)

    assert task.cancelled()
    assert orchestrator.abandoned == []

    gate.set()
    await asyncio.wait_for(orchestrator.abandon_done.wait(), 1)

    assert orchestrator.abandoned == [(orchestrator.prepared, "CHAT_TURN_FAILED")]


# --- drain_chat_turn ---


@pytest.mark.asyncio
async def test_drain_returns_the_last_terminal_event():
    orchestrator = FakeOrchestrator([STARTED, DELTA, ERROR, COMPLETED])
    seen = []

    terminal = await drain_chat_turn(
        orchestrator.stream_prepared(user_id=USER_ID, prepared=orchestrator.prepared),
        seen.append,
    )

    assert terminal == COMPLETED
    assert seen == [STARTED, DELTA, ERROR, COMPLETED]
    assert orchestrator.stream_closed


@pytest.mark.asyncio
async def test_drain_without_a_terminal_event_returns_none():
    orchestrator = FakeOrchestrator([STARTED, DELTA])

    terminal = await drain_chat_turn(
        orchestrator.stream_prepared(user_id=USER_ID, prepared=orchestrator.prepared)
    )

    assert terminal is None
    assert orchestrator.stream_closed


@pytest.mark.asyncio
async def test_drain_closes_the_stream_when_a_listener_fails():
    orchestrator = FakeOrchestrator([STARTED, COMPLETED])

    def listener(event):
        raise RuntimeError("listener broke")

    with pytest.raises(RuntimeError, match="listener broke"):
        await drain_chat_turn(
            orchestrator.stream_prepared(
                user_id=USER_ID, prepared=orchestrator.prepared
            ),
            listener,
        )

    assert orchestrator.stream_closed


@pytest.mark.asyncio
async def test_drain_accepts_a_stream_without_aclose():
    assert await drain_chat_turn(_PlainEvents(STARTED, COMPLETED)) == COMPLETED


# --- chat_job_result ---


def test_chat_result_without_a_terminal_event_fails():
    with pytest.raises(AiJobExecutionError) as exc_info:
        chat_job_result(None)

    assert exc_info.value.code == "CHAT_TURN_FAILED"


def test_chat_result_error_keeps_the_reply_error_code():
    with pytest.raises(AiJobExecutionError) as exc_info:
        chat_job_result(
            ChatSseEvent(event="message.error", data={"code": "CHAT_DAILY_LIMIT"})
        )

    assert exc_info.value.code == "CHAT_DAILY_LIMIT"


@pytest.mark.parametrize(
    "data",
    [{}, {"code": ""}, {"code": None}, {"code": 42}],
    ids=["missing", "empty", "none", "not-a-string"],
)
def test_chat_result_error_without_a_usable_code_is_a_generic_failure(data):
    with pytest.raises(AiJobExecutionError) as exc_info:
        chat_job_result(ChatSseEvent(event="message.error", data=data))

    assert exc_info.value.code == "CHAT_TURN_FAILED"


def test_chat_result_returns_the_reply_ids():
    assert chat_job_result(COMPLETED) == {
        "thread_id": "thread-1",
        "assistant_message_id": "msg-2",
    }


def test_chat_result_without_ids_returns_empty_ids():
    assert chat_job_result(ChatSseEvent(event="message.completed")) == {
        "thread_id": None,
        "assistant_message_id": None,
    }
