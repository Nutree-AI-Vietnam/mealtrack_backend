"""Streams a chat job's turn from the process that received the request.

The turn runs in its own task, so it keeps going when the client goes away;
the job records the outcome and the notifier pushes it if the user has not
seen the reply in the app. Terminal events carry ``ai_job_id`` and
``ai_job_status``. Any status but ``completed`` tells the client to follow
the job: ``queued`` means an automatic retry is pending, and null means the
status could not be read.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any

from src.app.services.ai_jobs.error_mapping import map_job_exception
from src.app.services.ai_jobs.executors import (
    CHAT_TERMINAL_EVENTS,
    AiJobInterrupt,
    chat_job_result,
    run_chat_turn,
)
from src.app.services.ai_jobs.recorder import AiJobAttemptRecorder
from src.app.services.ai_jobs.signals import AiJobCancelSignals
from src.app.services.ai_jobs.supervision import (
    AttemptOutcome,
    interrupt_work,
    run_detached,
    supervise_attempt,
)
from src.app.services.chat_turn_orchestrator import (
    ChatTurnOrchestrator,
    PreparedChatTurn,
)
from src.domain.model.ai_jobs import AiJobClaim, AiJobError, AiJobStatus
from src.domain.model.ai_jobs.policies import classify_ai_job_error
from src.domain.model.chat.models import ChatSseEvent

logger = logging.getLogger(__name__)

_INTERRUPT_GRACE_SECONDS = 5.0
_CLEANUP_TIMEOUT_SECONDS = 5.0


@dataclass(eq=False, slots=True)
class ChatJobSession:
    claim: AiJobClaim
    prepared: PreparedChatTurn
    queue: asyncio.Queue[ChatSseEvent | None] = field(default_factory=asyncio.Queue)
    # False once the client stops reading; later events are dropped.
    attached: bool = True
    task: asyncio.Task[None] | None = None

    def emit(self, event: ChatSseEvent) -> None:
        if self.attached:
            self.queue.put_nowait(event)


class ChatJobRunner:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], Any],
        cancel_signals: AiJobCancelSignals,
        lease_seconds: int,
        heartbeat_seconds: float,
        deadline_seconds: float,
        notify_grace_seconds: int,
        rng: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._recorder = AiJobAttemptRecorder(
            uow_factory=uow_factory,
            lease_seconds=lease_seconds,
            heartbeat_seconds=heartbeat_seconds,
            notify_grace_seconds=notify_grace_seconds,
            rng=rng,
        )
        self._cancel_signals = cancel_signals
        self._heartbeat_seconds = heartbeat_seconds
        self._deadline_seconds = deadline_seconds
        self._clock = clock
        self._tasks: set[asyncio.Task[None]] = set()

    def start(
        self,
        *,
        orchestrator: ChatTurnOrchestrator,
        claim: AiJobClaim,
        prepared: PreparedChatTurn,
    ) -> ChatJobSession:
        """Generate the prepared turn under the job's claim.

        The runner owns ``prepared`` from here on, including its provider slot.
        """
        session = ChatJobSession(claim=claim, prepared=prepared)
        task = asyncio.ensure_future(self._drive(orchestrator, session))
        session.task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        # End the stream however the task ends, even if it is cancelled
        # before its first step and never reaches its own cleanup.
        task.add_done_callback(lambda _: session.queue.put_nowait(None))
        return session

    async def stream(self, session: ChatJobSession) -> AsyncGenerator[ChatSseEvent]:
        """Yield the turn's events until it ends; leaving early detaches."""
        try:
            while True:
                event = await session.queue.get()
                if event is None:
                    return
                yield event
        finally:
            session.attached = False

    async def aclose(self, *, grace_seconds: float) -> None:
        """Let running turns finish for up to ``grace_seconds``, then stop the
        rest; their jobs go back to the queue for another process."""
        pending = set(self._tasks)
        if pending and grace_seconds > 0:
            _, pending = await asyncio.wait(pending, timeout=grace_seconds)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending, timeout=_CLEANUP_TIMEOUT_SECONDS * 2)

    async def _drive(
        self, orchestrator: ChatTurnOrchestrator, session: ChatJobSession
    ) -> None:
        claim = session.claim
        interrupt = AiJobInterrupt()
        cancel_event = self._cancel_signals.watch(claim.id)
        started = self._clock()
        work = asyncio.ensure_future(
            run_chat_turn(
                orchestrator,
                user_id=claim.user_id,
                prepared=session.prepared,
                interrupt=interrupt,
                on_event=lambda event: _relay(session, event),
            )
        )
        try:
            outcome = await supervise_attempt(
                work=work,
                heartbeat=lambda: self._recorder.heartbeat(claim),
                cancel_event=cancel_event,
                stop=None,
                interruptible=True,
                heartbeat_seconds=self._heartbeat_seconds,
                deadline=started + self._deadline_seconds,
                clock=self._clock,
            )
            session.emit(await self._record(session, work, interrupt, outcome))
        except asyncio.CancelledError:
            # Shutdown: hand the job back so another process retries it.
            interrupt.code = "WORKER_INTERRUPTED"
            work.cancel()
            await run_detached(
                self._recorder.fail(claim, classify_ai_job_error("WORKER_INTERRUPTED")),
                timeout=_CLEANUP_TIMEOUT_SECONDS,
                description="record interrupted chat turn",
            )
            session.emit(_error_event(session, "WORKER_INTERRUPTED", None))
            raise
        except Exception:
            # The job keeps its lease until it runs out; then the worker
            # replays the saved reply or generates it again.
            logger.exception("AI chat job runner failed", extra={"job_id": claim.id})
            work.cancel()
            session.emit(_error_event(session, "CHAT_TURN_FAILED", None))
        finally:
            self._cancel_signals.release(claim.id, cancel_event)

    async def _record(
        self,
        session: ChatJobSession,
        work: asyncio.Future[ChatSseEvent | None],
        interrupt: AiJobInterrupt,
        outcome: AttemptOutcome,
    ) -> ChatSseEvent:
        """Write the attempt's outcome and return the event that ends the stream."""
        claim = session.claim
        if outcome is AttemptOutcome.FINISHED:
            return await self._record_finished(session, work)
        interrupt.code = {
            AttemptOutcome.CANCELLED: "CHAT_STOPPED",
            AttemptOutcome.TIMED_OUT: "AI_TIMEOUT",
        }.get(outcome)
        await interrupt_work(work, timeout=_INTERRUPT_GRACE_SECONDS)
        if outcome is AttemptOutcome.CANCELLED:
            owned = await self._recorder.cancel(claim)
            status = (
                AiJobStatus.CANCELLED
                if owned
                else await self._recorder.current_status(claim)
            )
            return _error_event(session, "CHAT_STOPPED", status)
        if outcome is AttemptOutcome.TIMED_OUT:
            return await self._record_failure(
                session, classify_ai_job_error("AI_TIMEOUT")
            )
        self._recorder.lease_lost(claim)
        return _error_event(
            session, "CHAT_TURN_FAILED", await self._recorder.current_status(claim)
        )

    async def _record_finished(
        self, session: ChatJobSession, work: asyncio.Future[ChatSseEvent | None]
    ) -> ChatSseEvent:
        claim = session.claim
        try:
            terminal = work.result()
        except asyncio.CancelledError:
            return await self._record_failure(
                session, classify_ai_job_error("WORKER_INTERRUPTED")
            )
        except Exception as exc:
            return await self._record_failure(session, map_job_exception(exc))
        if terminal is None:
            return await self._record_failure(
                session, classify_ai_job_error("CHAT_TURN_FAILED")
            )
        if terminal.event == "message.error":
            code = terminal.data.get("code")
            error = classify_ai_job_error(
                code if isinstance(code, str) and code else "CHAT_TURN_FAILED"
            )
            if code != error.code:
                # Send the client the code the job records.
                terminal = ChatSseEvent(
                    event=terminal.event,
                    data={
                        **terminal.data,
                        "code": error.code,
                        "retryable": error.retryable,
                    },
                )
            status = await self._recorder.fail(claim, error)
            if status is None:
                status = await self._recorder.current_status(claim)
            return _with_job(terminal, claim, status)
        owned = await self._recorder.complete(claim, chat_job_result(terminal))
        status = (
            AiJobStatus.COMPLETED
            if owned
            else await self._recorder.current_status(claim)
        )
        return _with_job(terminal, claim, status)

    async def _record_failure(
        self, session: ChatJobSession, error: AiJobError
    ) -> ChatSseEvent:
        status = await self._recorder.fail(session.claim, error)
        if status is None:
            status = await self._recorder.current_status(session.claim)
        return _error_event(session, error.code, status)


def _relay(session: ChatJobSession, event: ChatSseEvent) -> None:
    if event.event in CHAT_TERMINAL_EVENTS:
        # Sent by the driver once the job has recorded the outcome.
        return
    if event.event == "message.started":
        event = ChatSseEvent(
            event=event.event, data={**event.data, "ai_job_id": session.claim.id}
        )
    session.emit(event)


def _with_job(
    event: ChatSseEvent, claim: AiJobClaim, status: AiJobStatus | None
) -> ChatSseEvent:
    return ChatSseEvent(
        event=event.event,
        data={
            **event.data,
            "ai_job_id": claim.id,
            "ai_job_status": status.value if status is not None else None,
        },
    )


def _error_event(
    session: ChatJobSession, code: str, status: AiJobStatus | None
) -> ChatSseEvent:
    return _with_job(
        ChatSseEvent(
            event="message.error",
            data={
                "code": code,
                "retryable": classify_ai_job_error(code).retryable,
                "assistant_message_id": session.prepared.claim.assistant_message.id,
            },
        ),
        session.claim,
        status,
    )
