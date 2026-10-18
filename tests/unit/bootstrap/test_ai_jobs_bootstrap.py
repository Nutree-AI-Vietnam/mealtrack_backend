"""Background AI jobs composition root: starting, stopping and wiring."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from tests.fixtures.fakes.fake_ai_jobs import StubSchemaProbe

import src.bootstrap.ai_jobs as ai_jobs_bootstrap
from src.app.services.ai_jobs import (
    ChatJobExecutor,
    MealScanJobExecutor,
    ParseTextJobExecutor,
)
from src.domain.model.ai_jobs import AiJobType
from src.infra.database.uow_async import AsyncUnitOfWork

LOGGER = "src.bootstrap.ai_jobs"

# Captured before any test patches a getter, so caches can always be cleared.
_CACHED_GETTERS = (
    ai_jobs_bootstrap.get_ai_job_wakeup,
    ai_jobs_bootstrap.get_ai_job_cancel_signals,
    ai_jobs_bootstrap.get_ai_jobs_schema_probe,
    ai_jobs_bootstrap.get_ai_job_service,
    ai_jobs_bootstrap.get_chat_job_runner,
    ai_jobs_bootstrap.get_push_token_service,
)


def _reset_module_state() -> None:
    # Tasks left by a failed test die with their event loop.
    ai_jobs_bootstrap._tasks.clear()
    ai_jobs_bootstrap._stop = None
    for getter in _CACHED_GETTERS:
        getter.cache_clear()


@pytest.fixture(autouse=True)
def clean_module_state():
    _reset_module_state()
    yield
    _reset_module_state()


class FakeLoop:
    """Stands in for the worker's or the notifier's run loop."""

    def __init__(
        self, *, ignore_stop: bool = False, error: Exception | None = None
    ) -> None:
        self.ignore_stop = ignore_stop
        self.error = error
        self.stops: list[asyncio.Event] = []
        self.finished = False

    async def run(self, stop: asyncio.Event) -> None:
        self.stops.append(stop)
        if self.error is not None:
            raise self.error
        if self.ignore_stop:
            await asyncio.Event().wait()
        await stop.wait()
        self.finished = True


def _settings(**overrides) -> SimpleNamespace:
    values = {
        "AI_JOBS_ENABLED": True,
        "AI_JOBS_WORKER_ENABLED": True,
        "AI_JOBS_CHAT_BACKGROUND_ENABLED": True,
        "AI_JOBS_WORKER_CONCURRENCY": 2,
        "AI_JOBS_GLOBAL_CAPACITY": 4,
        "AI_JOBS_LEASE_SECONDS": 120,
        "AI_JOBS_HEARTBEAT_SECONDS": 2.0,
        "AI_JOBS_EXECUTION_DEADLINE_SECONDS": 90,
        "AI_JOBS_POLL_SECONDS": 1.0,
        "AI_JOBS_NOTIFY_GRACE_SECONDS": 8,
    }
    return SimpleNamespace(**{**values, **overrides})


@pytest.fixture
def env(monkeypatch):
    """Replaces everything the module reaches outside the process."""
    env = SimpleNamespace(
        settings=_settings(),
        probe=StubSchemaProbe(),
        worker=FakeLoop(),
        notifier=FakeLoop(),
        push_executor=object(),
    )
    # Read at call time, so a test can swap in another loop before starting.
    env.AiJobWorker = MagicMock(side_effect=lambda **_: env.worker)
    env.AiJobNotifier = MagicMock(side_effect=lambda **_: env.notifier)
    env.FcmPushSender = MagicMock()
    env.get_push_executor = MagicMock(return_value=env.push_executor)
    env.shutdown_push_executor = MagicMock()
    monkeypatch.setattr(ai_jobs_bootstrap, "get_settings", lambda: env.settings)
    monkeypatch.setattr(
        ai_jobs_bootstrap, "get_ai_jobs_schema_probe", lambda: env.probe
    )
    for name in (
        "AiJobWorker",
        "AiJobNotifier",
        "FcmPushSender",
        "get_push_executor",
        "shutdown_push_executor",
    ):
        monkeypatch.setattr(ai_jobs_bootstrap, name, getattr(env, name))
    return env


def _records(caplog, level: int = logging.NOTSET) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == LOGGER and record.levelno >= level
    ]


def _messages(caplog, level: int = logging.NOTSET) -> list[str]:
    return [record.getMessage() for record in _records(caplog, level)]


@pytest.mark.asyncio
@pytest.mark.parametrize("jobs_enabled", [False, True])
async def test_start_runs_nothing_when_this_process_has_no_worker(
    env, caplog, jobs_enabled
):
    env.settings = _settings(AI_JOBS_WORKER_ENABLED=False, AI_JOBS_ENABLED=jobs_enabled)
    executors_factory = MagicMock()
    caplog.set_level(logging.INFO, logger=LOGGER)

    assert await ai_jobs_bootstrap.start_ai_jobs(executors_factory) is False

    executors_factory.assert_not_called()
    env.AiJobWorker.assert_not_called()
    env.AiJobNotifier.assert_not_called()
    assert env.probe.calls == 0
    assert ai_jobs_bootstrap._tasks == []
    assert ai_jobs_bootstrap._stop is None
    expected = (
        [
            "AI jobs are enabled but this process runs no worker; "
            "make sure another process sets AI_JOBS_WORKER_ENABLED"
        ]
        if jobs_enabled
        else []
    )
    assert _messages(caplog) == expected


@pytest.mark.asyncio
async def test_start_runs_the_worker_and_the_notifier_once(env, caplog):
    executors = {AiJobType.CHAT: object()}
    caplog.set_level(logging.INFO, logger=LOGGER)

    try:
        assert await ai_jobs_bootstrap.start_ai_jobs(lambda: executors) is True
        second_factory = MagicMock()
        assert await ai_jobs_bootstrap.start_ai_jobs(second_factory) is False
        await asyncio.sleep(0)

        # A second start neither probes the tables nor builds executors.
        second_factory.assert_not_called()
        assert env.probe.calls == 1

        env.AiJobWorker.assert_called_once_with(
            uow_factory=AsyncUnitOfWork,
            executors=executors,
            cancel_signals=ai_jobs_bootstrap.get_ai_job_cancel_signals(),
            wakeup=ai_jobs_bootstrap.get_ai_job_wakeup(),
            concurrency=2,
            global_capacity=4,
            lease_seconds=120,
            heartbeat_seconds=2.0,
            deadline_seconds=90,
            poll_seconds=1.0,
            notify_grace_seconds=8,
            ready=env.probe.ready,
        )
        env.FcmPushSender.assert_called_once_with(executor=env.push_executor)
        env.AiJobNotifier.assert_called_once_with(
            uow_factory=AsyncUnitOfWork,
            sender=env.FcmPushSender.return_value,
            ready=env.probe.ready,
        )
        stop = ai_jobs_bootstrap._stop
        assert stop is not None and not stop.is_set()
        assert len(ai_jobs_bootstrap._tasks) == 2
        assert env.worker.stops == [stop]
        assert env.notifier.stops == [stop]
        assert _messages(caplog) == ["AI job worker started"]
    finally:
        await ai_jobs_bootstrap.stop_ai_jobs_background(timeout=1.0)


@pytest.mark.asyncio
async def test_start_warns_but_still_runs_when_the_tables_are_missing(env, caplog):
    env.probe.is_ready = False
    caplog.set_level(logging.INFO, logger=LOGGER)

    try:
        assert await ai_jobs_bootstrap.start_ai_jobs(dict) is True

        assert env.probe.calls == 1
        assert _messages(caplog, logging.WARNING) == [
            "AI job tables are not migrated; the worker idles until they are"
        ]
        assert len(ai_jobs_bootstrap._tasks) == 2
        assert "AI job worker started" in _messages(caplog)
    finally:
        await ai_jobs_bootstrap.stop_ai_jobs_background(timeout=1.0)


@pytest.mark.asyncio
async def test_stop_does_nothing_when_nothing_runs(env):
    wakeup = ai_jobs_bootstrap.get_ai_job_wakeup()

    await ai_jobs_bootstrap.stop_ai_jobs_background(timeout=1.0)

    assert wakeup.generation == 0
    assert ai_jobs_bootstrap._tasks == []
    assert ai_jobs_bootstrap._stop is None


@pytest.mark.asyncio
async def test_stop_signals_both_loops_and_waits_for_them(env, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    wakeup = ai_jobs_bootstrap.get_ai_job_wakeup()
    assert ai_jobs_bootstrap.start_ai_jobs_background({}) is True
    tasks = list(ai_jobs_bootstrap._tasks)
    stop = ai_jobs_bootstrap._stop

    await ai_jobs_bootstrap.stop_ai_jobs_background(timeout=1.0)

    assert stop.is_set()
    # Idle worker lanes wake at once instead of sleeping out their poll.
    assert wakeup.generation == 1
    assert all(task.done() and not task.cancelled() for task in tasks)
    assert env.worker.finished and env.notifier.finished
    assert ai_jobs_bootstrap._tasks == []
    assert ai_jobs_bootstrap._stop is None
    assert _messages(caplog, logging.ERROR) == []


@pytest.mark.asyncio
async def test_stop_cancels_a_loop_that_outlives_the_timeout(env, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    env.worker = FakeLoop(ignore_stop=True)
    assert ai_jobs_bootstrap.start_ai_jobs_background({}) is True
    worker_task, notifier_task = ai_jobs_bootstrap._tasks

    await ai_jobs_bootstrap.stop_ai_jobs_background(timeout=0.01)

    assert worker_task.cancelled()
    assert notifier_task.done() and not notifier_task.cancelled()
    assert env.notifier.finished and not env.worker.finished
    assert ai_jobs_bootstrap._tasks == []
    assert ai_jobs_bootstrap._stop is None
    # A loop cancelled on shutdown is not an unexpected exit.
    assert _messages(caplog, logging.ERROR) == []


@pytest.mark.asyncio
async def test_a_loop_that_crashes_is_logged(env, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    env.worker = FakeLoop(error=RuntimeError("boom"))
    assert ai_jobs_bootstrap.start_ai_jobs_background({}) is True
    worker_task = ai_jobs_bootstrap._tasks[0]

    try:
        async with asyncio.timeout(1):
            await asyncio.wait([worker_task])
        await asyncio.sleep(0)

        records = _records(caplog, logging.ERROR)
        assert [record.getMessage() for record in records] == [
            "AI job worker stopped unexpectedly"
        ]
        assert isinstance(records[0].exc_info[1], RuntimeError)
    finally:
        await ai_jobs_bootstrap.stop_ai_jobs_background(timeout=1.0)
    assert env.notifier.finished


@pytest.mark.asyncio
async def test_worker_starts_again_after_a_stop(env):
    assert ai_jobs_bootstrap.start_ai_jobs_background({}) is True
    first_stop = ai_jobs_bootstrap._stop
    await ai_jobs_bootstrap.stop_ai_jobs_background(timeout=1.0)
    env.worker, env.notifier = FakeLoop(), FakeLoop()

    try:
        assert ai_jobs_bootstrap.start_ai_jobs_background({}) is True
        second_stop = ai_jobs_bootstrap._stop
        assert second_stop is not first_stop
        assert not second_stop.is_set()
        assert len(ai_jobs_bootstrap._tasks) == 2
    finally:
        await ai_jobs_bootstrap.stop_ai_jobs_background(timeout=1.0)
    assert env.AiJobWorker.call_count == 2
    assert env.worker.finished and env.notifier.finished


@pytest.mark.asyncio
async def test_shutdown_skips_a_chat_runner_that_was_never_built(env):
    await ai_jobs_bootstrap.shutdown_ai_jobs()

    assert ai_jobs_bootstrap.get_chat_job_runner.cache_info().currsize == 0
    env.shutdown_push_executor.assert_called_once_with()


@pytest.mark.asyncio
async def test_shutdown_closes_the_chat_runner_and_stops_the_worker(env, monkeypatch):
    runner = ai_jobs_bootstrap.get_chat_job_runner()
    aclose = AsyncMock()
    monkeypatch.setattr(runner, "aclose", aclose)
    assert ai_jobs_bootstrap.start_ai_jobs_background({}) is True
    tasks = list(ai_jobs_bootstrap._tasks)

    await ai_jobs_bootstrap.shutdown_ai_jobs()

    aclose.assert_awaited_once_with(grace_seconds=10.0)
    assert all(task.done() and not task.cancelled() for task in tasks)
    assert ai_jobs_bootstrap._tasks == []
    assert ai_jobs_bootstrap._stop is None
    env.shutdown_push_executor.assert_called_once_with()


@pytest.mark.asyncio
async def test_shutdown_logs_a_failed_step_and_finishes_the_others(
    env, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger=LOGGER)
    runner = ai_jobs_bootstrap.get_chat_job_runner()
    monkeypatch.setattr(runner, "aclose", AsyncMock(side_effect=RuntimeError("boom")))
    assert ai_jobs_bootstrap.start_ai_jobs_background({}) is True
    tasks = list(ai_jobs_bootstrap._tasks)

    await ai_jobs_bootstrap.shutdown_ai_jobs()

    records = _records(caplog, logging.ERROR)
    assert [record.getMessage() for record in records] == [
        "AI job shutdown step failed"
    ]
    assert isinstance(records[0].exc_info[1], RuntimeError)
    assert all(task.done() and not task.cancelled() for task in tasks)
    assert ai_jobs_bootstrap._tasks == []
    env.shutdown_push_executor.assert_called_once_with()


def test_chat_runner_and_service_share_one_set_of_cancel_signals(env):
    runner = ai_jobs_bootstrap.get_chat_job_runner()
    service = ai_jobs_bootstrap.get_ai_job_service()

    assert ai_jobs_bootstrap.get_chat_job_runner() is runner
    assert ai_jobs_bootstrap.get_ai_job_service() is service
    # A cancel request handled by this process must reach its running turns.
    signals = ai_jobs_bootstrap.get_ai_job_cancel_signals()
    assert runner._cancel_signals is signals
    assert service._cancel_signals is signals


def test_build_executors_covers_every_job_type():
    send = AsyncMock()
    orchestrator_factory = MagicMock()
    parse_text_result = MagicMock()

    executors = ai_jobs_bootstrap.build_ai_job_executors(
        send=send,
        orchestrator_factory=orchestrator_factory,
        parse_text_result=parse_text_result,
    )

    assert set(executors) == set(AiJobType)
    meal_scan = executors[AiJobType.MEAL_SCAN]
    assert isinstance(meal_scan, MealScanJobExecutor)
    assert meal_scan._send is send
    assert meal_scan._uow_factory is AsyncUnitOfWork
    parse_text = executors[AiJobType.PARSE_TEXT]
    assert isinstance(parse_text, ParseTextJobExecutor)
    assert parse_text._send is send
    assert parse_text._to_result is parse_text_result
    chat = executors[AiJobType.CHAT]
    assert isinstance(chat, ChatJobExecutor)
    assert chat._orchestrator_factory is orchestrator_factory
