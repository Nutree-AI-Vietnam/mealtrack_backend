"""Client-facing AI job use cases: enqueue, observe, retry, cancel, ack."""

from dataclasses import dataclass
from datetime import timedelta
from unittest.mock import patch
from uuid import UUID

import pytest
from tests.fixtures.fakes.fake_ai_jobs import (
    JOB_ID,
    NOW,
    USER_ID,
    FakeAiJobsUow,
    StubSchemaProbe,
    make_claim,
    make_job,
)

from src.app.services.ai_jobs.service import AiJobService, new_chat_job
from src.app.services.ai_jobs.signals import AiJobCancelSignals, AiJobWakeup
from src.domain.exceptions.ai_job_exceptions import (
    AiJobConflictError,
    AiJobNotFoundError,
    AiJobNotRetryableError,
    AiJobsUnavailableError,
)
from src.domain.model.ai_jobs import (
    AI_JOB_MAX_ATTEMPTS,
    AiJobListState,
    AiJobStatus,
    AiJobType,
    NewAiJob,
)
from src.domain.model.ai_jobs.policies import ai_job_request_fingerprint

LEASE_SECONDS = 120
PARSE_REQUEST = {"text": "2 eggs", "current_items": None}
PARSE_FINGERPRINT = ai_job_request_fingerprint(AiJobType.PARSE_TEXT, PARSE_REQUEST)
SCAN_REQUEST = {
    "image_url": "https://res.cloudinary.com/demo/image/upload/mealtrack/abc.jpg",
    "public_id": "mealtrack/abc",
    "user_description": None,
    "target_date": "2026-10-13",
    "scan_mode": "meal",
    "label_crop_image_url": None,
    "label_crop_public_id": None,
    "crop_metadata": None,
}
SCAN_FINGERPRINT = ai_job_request_fingerprint(AiJobType.MEAL_SCAN, SCAN_REQUEST)


@dataclass
class _Harness:
    uow: FakeAiJobsUow
    probe: StubSchemaProbe
    wakeup: AiJobWakeup
    signals: AiJobCancelSignals
    service: AiJobService


def _harness(
    *, ready: bool = True, enabled: bool = True, chat_background: bool = True
) -> _Harness:
    uow = FakeAiJobsUow()
    probe = StubSchemaProbe(ready)
    wakeup = AiJobWakeup()
    signals = AiJobCancelSignals()
    service = AiJobService(
        uow_factory=uow,
        schema_probe=probe,
        wakeup=wakeup,
        cancel_signals=signals,
        enabled=enabled,
        chat_background_enabled=chat_background,
        lease_seconds=LEASE_SECONDS,
    )
    return _Harness(uow, probe, wakeup, signals, service)


def _chat_job(**overrides) -> NewAiJob:
    kwargs = {
        "job_id": JOB_ID,
        "user_id": USER_ID,
        "language": "vi",
        "content": "  What should I eat tonight?  ",
        "idempotency_key": "idem-1",
        "locale": "vi-VN",
        "header_timezone": "Asia/Ho_Chi_Minh",
        "user_language": "vi",
    }
    kwargs.update(overrides)
    return new_chat_job(**kwargs)


async def _enqueue(
    service: AiJobService,
    *,
    job_type: AiJobType = AiJobType.PARSE_TEXT,
    request: dict | None = None,
    language: str = "en",
):
    return await service.enqueue(
        job_id=JOB_ID,
        user_id=USER_ID,
        job_type=job_type,
        language=language,
        request=dict(PARSE_REQUEST) if request is None else request,
    )


_OPERATIONS = {
    "capabilities": lambda service: service.capabilities(),
    "enqueue": lambda service: _enqueue(service),
    "get": lambda service: service.get(user_id=USER_ID, job_id=JOB_ID),
    "list_by_ids": lambda service: service.list_by_ids(
        user_id=USER_ID, job_ids=[JOB_ID]
    ),
    "list_by_state": lambda service: service.list_by_state(
        user_id=USER_ID, state=AiJobListState.OPEN
    ),
    "retry": lambda service: service.retry(user_id=USER_ID, job_id=JOB_ID),
    "cancel": lambda service: service.cancel(user_id=USER_ID, job_id=JOB_ID),
    "mark_seen": lambda service: service.mark_seen(user_id=USER_ID, job_ids=[JOB_ID]),
    "start_chat_job": lambda service: service.start_chat_job(_chat_job()),
}
_NEW_WORK = ["enqueue", "retry", "start_chat_job"]
_OBSERVE_AND_STOP = sorted(set(_OPERATIONS) - set(_NEW_WORK))


# --- new_chat_job ----------------------------------------------------------


def test_new_chat_job_stores_what_a_worker_needs_to_replay_the_turn():
    job = _chat_job(
        intent="next_meal", function_name="suggest_meal", function_args={"n": 3}
    )

    assert job.id == JOB_ID
    assert job.user_id == USER_ID
    assert job.job_type is AiJobType.CHAT
    assert job.language == "vi"
    assert job.dedupe_key == "idem-1"
    assert job.max_attempts == AI_JOB_MAX_ATTEMPTS
    assert job.request_payload == {
        "content": "What should I eat tonight?",
        "idempotency_key": "idem-1",
        "intent": "next_meal",
        "function_name": "suggest_meal",
        "function_args": {"n": 3},
        "locale": "vi-VN",
        "header_timezone": "Asia/Ho_Chi_Minh",
        "user_language": "vi",
    }


def test_new_chat_job_fingerprints_the_turn_without_its_context():
    assert _chat_job().request_fingerprint == ai_job_request_fingerprint(
        AiJobType.CHAT,
        {
            "content": "What should I eat tonight?",
            "idempotency_key": "idem-1",
            "intent": None,
            "function_name": None,
            "function_args": None,
        },
    )


def test_new_chat_job_fingerprint_ignores_locale_timezone_and_padding():
    same_turn = _chat_job(
        content="What should I eat tonight?",
        locale="en-US",
        header_timezone="UTC",
        user_language="en",
        language="en",
    )

    assert same_turn.request_fingerprint == _chat_job().request_fingerprint


@pytest.mark.parametrize(
    "change",
    [
        {"content": "Something else"},
        {"idempotency_key": "idem-2"},
        {"intent": "next_meal"},
        {"function_name": "suggest_meal"},
        {"function_args": {"n": 3}},
    ],
    ids=["content", "idempotency_key", "intent", "function_name", "function_args"],
)
def test_new_chat_job_fingerprint_changes_with_the_turn(change):
    assert _chat_job(**change).request_fingerprint != _chat_job().request_fingerprint


# --- guards shared by every use case ----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", sorted(_OPERATIONS))
async def test_every_use_case_reports_a_missing_schema(operation):
    h = _harness(ready=False)

    with pytest.raises(AiJobsUnavailableError) as excinfo:
        await _OPERATIONS[operation](h.service)

    assert excinfo.value.reason == "schema_missing"
    assert h.uow.opened == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", _NEW_WORK)
async def test_new_work_is_refused_while_jobs_are_switched_off(operation):
    h = _harness(enabled=False)

    with pytest.raises(AiJobsUnavailableError) as excinfo:
        await _OPERATIONS[operation](h.service)

    assert excinfo.value.reason == "disabled"
    assert h.probe.calls == 0
    assert h.uow.opened == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", _OBSERVE_AND_STOP)
async def test_observing_and_stopping_jobs_still_work_while_switched_off(operation):
    h = _harness(enabled=False)
    h.uow.ai_jobs.get.return_value = make_job()
    h.uow.ai_jobs.list_by_ids.return_value = []
    h.uow.ai_jobs.list_by_state.return_value = []
    h.uow.ai_jobs.request_cancel.return_value = make_job(status=AiJobStatus.CANCELLED)
    h.uow.ai_jobs.mark_seen.return_value = 1

    await _OPERATIONS[operation](h.service)

    assert h.probe.calls == 1


# --- capabilities ----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "chat_background", "chat_expected"),
    [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ],
)
async def test_capabilities_describe_what_this_server_runs(
    enabled, chat_background, chat_expected
):
    h = _harness(enabled=enabled, chat_background=chat_background)

    assert await h.service.capabilities() == {
        "ai_jobs": enabled,
        "types": ["chat", "meal_scan", "parse_text"],
        "max_attempts": 4,
        "poll_interval_ms": 2500,
        "chat_background": chat_expected,
    }


# --- enqueue ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_jobs_cannot_be_enqueued_directly():
    h = _harness()

    with pytest.raises(ValueError, match="not enqueued directly"):
        await _enqueue(h.service, job_type=AiJobType.CHAT, request={"content": "hi"})

    assert h.probe.calls == 0
    h.uow.ai_jobs.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_creates_a_parse_text_job_and_wakes_the_worker():
    h = _harness()
    stored = make_job(request_fingerprint=PARSE_FINGERPRINT)
    h.uow.ai_jobs.create.return_value = (stored, True)

    result = await _enqueue(h.service, language="vi")

    assert result == (stored, True)
    h.uow.ai_jobs.create.assert_awaited_once_with(
        NewAiJob(
            id=JOB_ID,
            user_id=USER_ID,
            job_type=AiJobType.PARSE_TEXT,
            language="vi",
            request_payload=PARSE_REQUEST,
            request_fingerprint=PARSE_FINGERPRINT,
        )
    )
    assert h.wakeup.generation == 1
    assert h.uow.committed == 1


@pytest.mark.asyncio
async def test_meal_scan_jobs_get_a_fresh_meal_id_outside_the_fingerprint():
    h = _harness()
    h.uow.ai_jobs.create.return_value = (
        make_job(job_type=AiJobType.MEAL_SCAN, request_fingerprint=SCAN_FINGERPRINT),
        True,
    )
    request = dict(SCAN_REQUEST)

    await _enqueue(h.service, job_type=AiJobType.MEAL_SCAN, request=request)

    new_job = h.uow.ai_jobs.create.await_args.args[0]
    meal_id = new_job.request_payload["meal_id"]
    assert str(UUID(meal_id)) == meal_id
    assert new_job.request_payload == {**SCAN_REQUEST, "meal_id": meal_id}
    assert new_job.request_fingerprint == SCAN_FINGERPRINT
    assert new_job.dedupe_key is None
    assert request == SCAN_REQUEST


@pytest.mark.asyncio
async def test_resending_a_meal_scan_matches_although_a_new_meal_id_was_minted():
    h = _harness()
    stored = make_job(
        job_type=AiJobType.MEAL_SCAN, request_fingerprint=SCAN_FINGERPRINT
    )
    h.uow.ai_jobs.create.side_effect = [(stored, True), (stored, False)]

    first = await _enqueue(
        h.service, job_type=AiJobType.MEAL_SCAN, request=dict(SCAN_REQUEST)
    )
    second = await _enqueue(
        h.service, job_type=AiJobType.MEAL_SCAN, request=dict(SCAN_REQUEST)
    )

    assert first == (stored, True)
    assert second == (stored, False)
    sent = [call.args[0] for call in h.uow.ai_jobs.create.await_args_list]
    assert sent[0].request_payload["meal_id"] != sent[1].request_payload["meal_id"]
    assert sent[0].request_fingerprint == sent[1].request_fingerprint
    assert h.wakeup.generation == 1


@pytest.mark.asyncio
async def test_resending_the_same_request_returns_the_existing_job():
    h = _harness()
    existing = make_job(
        status=AiJobStatus.RUNNING, request_fingerprint=PARSE_FINGERPRINT
    )
    h.uow.ai_jobs.create.return_value = (existing, False)

    assert await _enqueue(h.service) == (existing, False)
    assert h.wakeup.generation == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "difference",
    [
        {"user_id": "u2"},
        {"job_type": AiJobType.MEAL_SCAN},
        {"request_fingerprint": "another-request"},
    ],
    ids=["other-user", "other-type", "other-input"],
)
async def test_reusing_a_job_id_for_another_request_conflicts(difference):
    h = _harness()
    existing = make_job(**{"request_fingerprint": PARSE_FINGERPRINT, **difference})
    h.uow.ai_jobs.create.return_value = (existing, False)

    with pytest.raises(AiJobConflictError) as excinfo:
        await _enqueue(h.service)

    assert excinfo.value.job_id == JOB_ID
    assert h.wakeup.generation == 0


@pytest.mark.asyncio
async def test_a_failed_insert_wakes_nobody():
    h = _harness()
    h.uow.ai_jobs.create.side_effect = RuntimeError("db down")

    with pytest.raises(RuntimeError):
        await _enqueue(h.service)

    assert h.wakeup.generation == 0
    assert h.uow.rolled_back == 1


# --- get / list ------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_the_users_job():
    h = _harness()
    job = make_job()
    h.uow.ai_jobs.get.return_value = job

    assert await h.service.get(user_id=USER_ID, job_id=JOB_ID) is job
    h.uow.ai_jobs.get.assert_awaited_once_with(user_id=USER_ID, job_id=JOB_ID)


@pytest.mark.asyncio
async def test_get_reports_unknown_or_foreign_jobs_as_not_found():
    h = _harness()
    h.uow.ai_jobs.get.return_value = None

    with pytest.raises(AiJobNotFoundError) as excinfo:
        await h.service.get(user_id=USER_ID, job_id=JOB_ID)

    assert excinfo.value.job_id == JOB_ID


@pytest.mark.asyncio
async def test_list_by_ids_asks_for_each_id_once_in_order():
    h = _harness()
    jobs = [make_job()]
    h.uow.ai_jobs.list_by_ids.return_value = jobs

    result = await h.service.list_by_ids(user_id=USER_ID, job_ids=["b", "a", "b"])

    assert result == jobs
    h.uow.ai_jobs.list_by_ids.assert_awaited_once_with(
        user_id=USER_ID, job_ids=["b", "a"]
    )


@pytest.mark.asyncio
async def test_list_by_ids_without_ids_skips_the_database():
    h = _harness()

    assert await h.service.list_by_ids(user_id=USER_ID, job_ids=[]) == []
    assert h.uow.opened == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("state", list(AiJobListState))
async def test_list_by_state_looks_back_one_day_for_at_most_twenty_jobs(state):
    h = _harness()
    jobs = [make_job()]
    h.uow.ai_jobs.list_by_state.return_value = jobs

    with patch("src.app.services.ai_jobs.service.utc_now", return_value=NOW):
        result = await h.service.list_by_state(user_id=USER_ID, state=state)

    assert result == jobs
    h.uow.ai_jobs.list_by_state.assert_awaited_once_with(
        user_id=USER_ID, state=state, since=NOW - timedelta(hours=24), limit=20
    )


# --- retry -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_requeues_the_job_and_wakes_the_worker():
    h = _harness()
    queued = make_job(status=AiJobStatus.QUEUED)
    h.uow.ai_jobs.request_retry.return_value = queued

    assert await h.service.retry(user_id=USER_ID, job_id=JOB_ID) is queued
    h.uow.ai_jobs.request_retry.assert_awaited_once_with(user_id=USER_ID, job_id=JOB_ID)
    assert h.wakeup.generation == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [AiJobNotRetryableError(JOB_ID, "completed"), AiJobNotFoundError(JOB_ID)],
    ids=["not-retryable", "not-found"],
)
async def test_a_refused_retry_wakes_nobody(error):
    h = _harness()
    h.uow.ai_jobs.request_retry.side_effect = error

    with pytest.raises(type(error)):
        await h.service.retry(user_id=USER_ID, job_id=JOB_ID)

    assert h.wakeup.generation == 0


# --- cancel ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancelling_a_running_job_signals_its_attempt_in_this_process():
    h = _harness()
    attempt = h.signals.watch(JOB_ID)
    h.uow.ai_jobs.request_cancel.return_value = make_job(status=AiJobStatus.RUNNING)

    job = await h.service.cancel(user_id=USER_ID, job_id=JOB_ID)

    assert job.status is AiJobStatus.RUNNING
    assert attempt.is_set()
    h.uow.ai_jobs.request_cancel.assert_awaited_once_with(
        user_id=USER_ID, job_id=JOB_ID
    )


@pytest.mark.asyncio
async def test_a_job_running_in_another_process_is_left_to_its_heartbeat():
    h = _harness()
    h.uow.ai_jobs.request_cancel.return_value = make_job(status=AiJobStatus.RUNNING)

    job = await h.service.cancel(user_id=USER_ID, job_id=JOB_ID)

    assert job.status is AiJobStatus.RUNNING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [AiJobStatus.CANCELLED, AiJobStatus.COMPLETED, AiJobStatus.FAILED]
)
async def test_cancel_signals_nothing_when_the_job_is_not_running(status):
    h = _harness()
    attempt = h.signals.watch(JOB_ID)
    h.uow.ai_jobs.request_cancel.return_value = make_job(status=status)

    job = await h.service.cancel(user_id=USER_ID, job_id=JOB_ID)

    assert job.status is status
    assert not attempt.is_set()


@pytest.mark.asyncio
async def test_cancelling_an_unknown_job_is_not_found():
    h = _harness()
    attempt = h.signals.watch(JOB_ID)
    h.uow.ai_jobs.request_cancel.side_effect = AiJobNotFoundError(JOB_ID)

    with pytest.raises(AiJobNotFoundError):
        await h.service.cancel(user_id=USER_ID, job_id=JOB_ID)

    assert not attempt.is_set()


# --- mark_seen -------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_seen_sends_each_job_once_and_returns_the_count():
    h = _harness()
    h.uow.ai_jobs.mark_seen.return_value = 2

    assert await h.service.mark_seen(user_id=USER_ID, job_ids=["a", "b", "a"]) == 2
    h.uow.ai_jobs.mark_seen.assert_awaited_once_with(
        user_id=USER_ID, job_ids=["a", "b"]
    )


@pytest.mark.asyncio
async def test_mark_seen_without_ids_skips_the_database():
    h = _harness()

    assert await h.service.mark_seen(user_id=USER_ID, job_ids=[]) == 0
    assert h.uow.opened == 0


# --- start_chat_job --------------------------------------------------------


@pytest.mark.asyncio
async def test_start_chat_job_registers_the_turn_under_a_lease():
    h = _harness()
    claim = make_claim(job_type=AiJobType.CHAT)
    h.uow.ai_jobs.start_chat_job.return_value = claim
    job = _chat_job()

    assert await h.service.start_chat_job(job) is claim
    h.uow.ai_jobs.start_chat_job.assert_awaited_once_with(
        job, lease_seconds=LEASE_SECONDS
    )
    assert h.uow.committed == 1
    assert h.wakeup.generation == 0


@pytest.mark.asyncio
async def test_start_chat_job_returns_none_when_the_turn_runs_elsewhere():
    h = _harness()
    h.uow.ai_jobs.start_chat_job.return_value = None

    assert await h.service.start_chat_job(_chat_job()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "chat_background", "ready", "reason"),
    [
        (True, False, True, "chat_disabled"),
        (False, False, True, "chat_disabled"),
        (False, True, True, "disabled"),
        (True, True, False, "schema_missing"),
    ],
)
async def test_start_chat_job_refusals(enabled, chat_background, ready, reason):
    h = _harness(enabled=enabled, chat_background=chat_background, ready=ready)

    with pytest.raises(AiJobsUnavailableError) as excinfo:
        await h.service.start_chat_job(_chat_job())

    assert excinfo.value.reason == reason
    h.uow.ai_jobs.start_chat_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_chat_job_passes_on_a_conflicting_turn():
    h = _harness()
    h.uow.ai_jobs.start_chat_job.side_effect = AiJobConflictError(JOB_ID)

    with pytest.raises(AiJobConflictError):
        await h.service.start_chat_job(_chat_job())


# --- find_active_chat_job_id -----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_background", [True, False])
async def test_find_active_chat_job_id_returns_the_running_turn(chat_background):
    h = _harness(chat_background=chat_background)
    h.uow.ai_jobs.find_active_chat_job_id.return_value = JOB_ID

    found = await h.service.find_active_chat_job_id(
        user_id=USER_ID, dedupe_key="idem-1"
    )

    assert found == JOB_ID
    h.uow.ai_jobs.find_active_chat_job_id.assert_awaited_once_with(
        user_id=USER_ID, dedupe_key="idem-1"
    )


@pytest.mark.asyncio
async def test_find_active_chat_job_id_is_skipped_while_jobs_are_switched_off():
    h = _harness(enabled=False)

    found = await h.service.find_active_chat_job_id(
        user_id=USER_ID, dedupe_key="idem-1"
    )

    assert found is None
    assert h.probe.calls == 0
    assert h.uow.opened == 0


@pytest.mark.asyncio
async def test_find_active_chat_job_id_finds_nothing_before_the_migration():
    h = _harness(ready=False)

    found = await h.service.find_active_chat_job_id(
        user_id=USER_ID, dedupe_key="idem-1"
    )

    assert found is None
    assert h.uow.opened == 0


@pytest.mark.asyncio
async def test_find_active_chat_job_id_swallows_lookup_failures():
    h = _harness()
    h.uow.ai_jobs.find_active_chat_job_id.side_effect = RuntimeError("db down")

    found = await h.service.find_active_chat_job_id(
        user_id=USER_ID, dedupe_key="idem-1"
    )

    assert found is None


@pytest.mark.asyncio
async def test_find_active_chat_job_id_swallows_probe_failures():
    class _BrokenProbe:
        async def ready(self) -> bool:
            raise ConnectionError("db down")

    service = AiJobService(
        uow_factory=FakeAiJobsUow(),
        schema_probe=_BrokenProbe(),
        wakeup=AiJobWakeup(),
        cancel_signals=AiJobCancelSignals(),
        enabled=True,
        chat_background_enabled=True,
        lease_seconds=LEASE_SECONDS,
    )

    found = await service.find_active_chat_job_id(user_id=USER_ID, dedupe_key="idem-1")

    assert found is None
