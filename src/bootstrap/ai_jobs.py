"""Composition root for background AI jobs.

Every API process enqueues and observes jobs. Processes with
``AI_JOBS_WORKER_ENABLED`` also run the worker lanes and the push notifier;
the worker drains queued jobs even while ``AI_JOBS_ENABLED`` stops new ones.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine, Mapping
from functools import lru_cache
from typing import Any

from src.app.services.ai_jobs import (
    AiJobCancelSignals,
    AiJobNotifier,
    AiJobService,
    AiJobsSchemaProbe,
    AiJobWakeup,
    AiJobWorker,
    ChatJobExecutor,
    ChatJobRunner,
    MealScanJobExecutor,
    ParseTextJobExecutor,
    PushTokenService,
)
from src.app.services.ai_jobs.executors import AiJobExecutor, Send
from src.app.services.chat_turn_orchestrator import ChatTurnOrchestrator
from src.domain.model.ai_jobs import AiJobType
from src.infra.adapters.fcm_push_sender import FcmPushSender
from src.infra.concurrency.executors import get_push_executor, shutdown_push_executor
from src.infra.config.settings import get_settings
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)

# Render allows 30 s between SIGTERM and SIGKILL. Jobs still running when the
# process dies keep their lease until it expires, then run again elsewhere.
_BACKGROUND_SHUTDOWN_TIMEOUT_SECONDS = 15.0
_CHAT_SHUTDOWN_GRACE_SECONDS = 10.0
_CANCEL_TIMEOUT_SECONDS = 5.0

_stop: asyncio.Event | None = None
_tasks: list[asyncio.Task[None]] = []


@lru_cache
def get_ai_job_wakeup() -> AiJobWakeup:
    return AiJobWakeup()


@lru_cache
def get_ai_job_cancel_signals() -> AiJobCancelSignals:
    return AiJobCancelSignals()


@lru_cache
def get_ai_jobs_schema_probe() -> AiJobsSchemaProbe:
    return AiJobsSchemaProbe(AsyncUnitOfWork)


@lru_cache
def get_ai_job_service() -> AiJobService:
    settings = get_settings()
    return AiJobService(
        uow_factory=AsyncUnitOfWork,
        schema_probe=get_ai_jobs_schema_probe(),
        wakeup=get_ai_job_wakeup(),
        cancel_signals=get_ai_job_cancel_signals(),
        enabled=settings.AI_JOBS_ENABLED,
        chat_background_enabled=settings.AI_JOBS_CHAT_BACKGROUND_ENABLED,
        lease_seconds=settings.AI_JOBS_LEASE_SECONDS,
    )


@lru_cache
def get_chat_job_runner() -> ChatJobRunner:
    settings = get_settings()
    return ChatJobRunner(
        uow_factory=AsyncUnitOfWork,
        cancel_signals=get_ai_job_cancel_signals(),
        lease_seconds=settings.AI_JOBS_LEASE_SECONDS,
        heartbeat_seconds=settings.AI_JOBS_HEARTBEAT_SECONDS,
        deadline_seconds=settings.AI_JOBS_EXECUTION_DEADLINE_SECONDS,
        notify_grace_seconds=settings.AI_JOBS_NOTIFY_GRACE_SECONDS,
    )


@lru_cache
def get_push_token_service() -> PushTokenService:
    return PushTokenService(
        uow_factory=AsyncUnitOfWork, schema_probe=get_ai_jobs_schema_probe()
    )


def build_ai_job_executors(
    *,
    send: Send,
    orchestrator_factory: Callable[[], ChatTurnOrchestrator],
    parse_text_result: Callable[[Any], dict[str, Any]],
) -> dict[AiJobType, AiJobExecutor]:
    return {
        AiJobType.MEAL_SCAN: MealScanJobExecutor(
            send=send, uow_factory=AsyncUnitOfWork
        ),
        AiJobType.PARSE_TEXT: ParseTextJobExecutor(
            send=send, to_result=parse_text_result
        ),
        AiJobType.CHAT: ChatJobExecutor(orchestrator_factory=orchestrator_factory),
    }


async def start_ai_jobs(
    executors_factory: Callable[[], Mapping[AiJobType, AiJobExecutor]],
) -> bool:
    """Start the worker in this process if it is configured to run one."""
    settings = get_settings()
    if not settings.AI_JOBS_WORKER_ENABLED:
        if settings.AI_JOBS_ENABLED:
            logger.info(
                "AI jobs are enabled but this process runs no worker; "
                "make sure another process sets AI_JOBS_WORKER_ENABLED"
            )
        return False
    if _tasks:
        return False
    if not await get_ai_jobs_schema_probe().ready():
        logger.warning(
            "AI job tables are not migrated; the worker idles until they are"
        )
    started = start_ai_jobs_background(executors_factory())
    if started:
        logger.info("AI job worker started")
    return started


def start_ai_jobs_background(executors: Mapping[AiJobType, AiJobExecutor]) -> bool:
    """Run the worker lanes and the notifier; False if they already run."""
    global _stop
    if _tasks:
        return False
    settings = get_settings()
    probe = get_ai_jobs_schema_probe()
    worker = AiJobWorker(
        uow_factory=AsyncUnitOfWork,
        executors=executors,
        cancel_signals=get_ai_job_cancel_signals(),
        wakeup=get_ai_job_wakeup(),
        concurrency=settings.AI_JOBS_WORKER_CONCURRENCY,
        global_capacity=settings.AI_JOBS_GLOBAL_CAPACITY,
        lease_seconds=settings.AI_JOBS_LEASE_SECONDS,
        heartbeat_seconds=settings.AI_JOBS_HEARTBEAT_SECONDS,
        deadline_seconds=settings.AI_JOBS_EXECUTION_DEADLINE_SECONDS,
        poll_seconds=settings.AI_JOBS_POLL_SECONDS,
        notify_grace_seconds=settings.AI_JOBS_NOTIFY_GRACE_SECONDS,
        ready=probe.ready,
    )
    notifier = AiJobNotifier(
        uow_factory=AsyncUnitOfWork,
        sender=FcmPushSender(executor=get_push_executor()),
        ready=probe.ready,
    )
    stop = asyncio.Event()
    _stop = stop
    _tasks.extend(
        [
            _spawn(worker.run(stop), "AI job worker"),
            _spawn(notifier.run(stop), "AI job notifier"),
        ]
    )
    return True


async def stop_ai_jobs_background(*, timeout: float) -> None:
    """Stop the worker and the notifier; running attempts get ``timeout``.

    Parse-text and chat attempts stop at once and go back to the queue. A
    meal scan is cancelled if it is still running when the timeout passes.
    """
    global _stop
    if not _tasks:
        return
    tasks = list(_tasks)
    if _stop is not None:
        _stop.set()
    get_ai_job_wakeup().notify()
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending, timeout=_CANCEL_TIMEOUT_SECONDS)
    _tasks.clear()
    _stop = None


async def shutdown_ai_jobs() -> None:
    """Stop all background AI work in this process."""
    results = await asyncio.gather(
        _close_chat_runner(),
        stop_ai_jobs_background(timeout=_BACKGROUND_SHUTDOWN_TIMEOUT_SECONDS),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            logger.error("AI job shutdown step failed", exc_info=result)
    shutdown_push_executor()


async def _close_chat_runner() -> None:
    # Building a runner just to close it would be wasted work.
    if get_chat_job_runner.cache_info().currsize:
        await get_chat_job_runner().aclose(grace_seconds=_CHAT_SHUTDOWN_GRACE_SECONDS)


def _spawn(coro: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
    task = asyncio.ensure_future(coro)
    task.add_done_callback(lambda done: _log_exit(done, name))
    return task


def _log_exit(task: asyncio.Task[None], name: str) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("%s stopped unexpectedly", name, exc_info=exc)
