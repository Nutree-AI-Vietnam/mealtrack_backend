"""Unit tests for AsyncUnitOfWork."""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

import src.infra.database.uow_async as uow_async
from src.infra.database.uow_async import AsyncUnitOfWork


@pytest.mark.asyncio
async def test_async_uow_commits_on_clean_exit():
    mock_session = AsyncMock()

    with patch("src.infra.database.uow_async.AsyncSessionLocal") as mock_factory:
        mock_factory.return_value = mock_session
        async with AsyncUnitOfWork():
            pass

    mock_session.__aenter__.assert_not_awaited()
    mock_session.commit.assert_awaited_once()
    mock_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_uow_rolls_back_on_exception():
    mock_session = AsyncMock()

    with patch("src.infra.database.uow_async.AsyncSessionLocal") as mock_factory:
        mock_factory.return_value = mock_session
        with pytest.raises(ValueError):
            async with AsyncUnitOfWork():
                raise ValueError("boom")

    mock_session.__aenter__.assert_not_awaited()
    mock_session.rollback.assert_awaited_once()
    mock_session.commit.assert_not_awaited()
    mock_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_uow_exposes_repos():
    mock_session = AsyncMock()

    with patch("src.infra.database.uow_async.AsyncSessionLocal") as mock_factory:
        mock_factory.return_value = mock_session
        async with AsyncUnitOfWork() as uow:
            assert hasattr(uow, "meals")
            assert hasattr(uow, "users")
            assert hasattr(uow, "weekly_budgets")
            assert hasattr(uow, "cheat_days")
            assert hasattr(uow, "hydration_entries")
            assert hasattr(uow, "subscriptions")
            assert hasattr(uow, "notifications")
            assert hasattr(uow, "engagement_pushes")
            assert hasattr(uow, "saved_suggestions")
            assert hasattr(uow, "food_references")
            assert hasattr(uow, "catalog_recipes")
            assert hasattr(uow, "meal_translations")


@pytest.mark.asyncio
async def test_async_uow_raises_clear_error_when_session_factory_missing():
    with patch.object(uow_async, "AsyncSessionLocal", None):
        with pytest.raises(RuntimeError, match="AsyncSessionLocal is not initialized"):
            async with AsyncUnitOfWork():
                pass


@pytest.mark.asyncio
async def test_async_uow_read_only_rolls_back_and_skips_commit():
    mock_session = AsyncMock()

    with patch("src.infra.database.uow_async.AsyncSessionLocal") as mock_factory:
        mock_factory.return_value = mock_session
        async with AsyncUnitOfWork(read_only=True) as uow:
            assert uow.is_read_only is True

    mock_session.rollback.assert_awaited_once()
    mock_session.commit.assert_not_awaited()
    mock_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_uow_read_only_factory_creates_read_only_instance():
    uow = AsyncUnitOfWork.read_only()
    assert uow.is_read_only is True


@pytest.mark.asyncio
async def test_async_uow_finishes_close_when_chat_request_is_cancelled(caplog):
    """PYTHON-HP: chat sessions are AsyncUnitOfWork, not get_async_db.

    Cancelling during close must still finish the close. On main, __aexit__
    awaits session.close() on the cancelled request, so the close is abandoned
    and NullPool logs "Exception terminating connection".
    """
    started = asyncio.Event()
    finished = asyncio.Event()

    class _Session:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

        async def close(self) -> None:
            started.set()
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                logging.getLogger("sqlalchemy.pool.impl.NullPool").error(
                    "Exception terminating connection %r",
                    self,
                    exc_info=True,
                )
                raise
            finished.set()

    async def _chat_request() -> None:
        async with AsyncUnitOfWork():
            return None

    with patch(
        "src.infra.database.uow_async.AsyncSessionLocal", return_value=_Session()
    ):
        request = asyncio.create_task(_chat_request())
        await asyncio.wait_for(started.wait(), timeout=1)
        request.cancel()
        with caplog.at_level(logging.ERROR):
            with pytest.raises(asyncio.CancelledError):
                await request
            await asyncio.wait_for(finished.wait(), timeout=1)

    assert finished.is_set()
    assert not any(
        "Exception terminating connection" in record.getMessage()
        for record in caplog.records
    )


def _nullpool_close_on_cancel(session: object, phase: str) -> None:
    """PlannerNullPool logs a graceful close that is cancelled with the task."""
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        logging.getLogger("src.infra.database.planner_pool.PlannerNullPool").error(
            "Exception closing connection %r during %s",
            session,
            phase,
            exc_info=True,
        )
        raise asyncio.CancelledError


@pytest.mark.asyncio
async def test_cancelled_meal_plan_request_closes_off_the_request_task(caplog):
    """PYTHON-KK: GET /v1/meal-plans/current holds a session across Redis.

    Cancelling that request used to roll the session back on the request
    task. Session.rollback returns the connection, and PlannerNullPool's
    graceful close then logs "Exception closing connection".
    """
    entered = asyncio.Event()
    events: list[str] = []

    class _Session:
        async def commit(self) -> None:
            raise AssertionError("a cancelled request must not commit")

        async def rollback(self) -> None:
            _nullpool_close_on_cancel(self, "rollback")
            events.append("rollback")

        async def close(self) -> None:
            _nullpool_close_on_cancel(self, "close")
            events.append("close")

    async def _request() -> None:
        async with AsyncUnitOfWork():
            entered.set()
            await asyncio.sleep(30)

    with patch(
        "src.infra.database.uow_async.AsyncSessionLocal", return_value=_Session()
    ):
        request = asyncio.create_task(_request())
        await asyncio.wait_for(entered.wait(), timeout=1)
        request.cancel()
        with caplog.at_level(logging.ERROR):
            with pytest.raises(asyncio.CancelledError):
                await request
            for _ in range(50):
                if events == ["rollback", "close"]:
                    break
                await asyncio.sleep(0.01)

    assert events == ["rollback", "close"]
    assert not any(
        "Exception closing connection" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_cancelled_request_still_reports_a_real_rollback_failure(caplog):
    """Cancellation is benign. A rollback error on the shielded task is not."""
    entered = asyncio.Event()
    closed = asyncio.Event()

    class _Session:
        async def commit(self) -> None:
            raise AssertionError("a cancelled request must not commit")

        async def rollback(self) -> None:
            raise RuntimeError("rollback failed")

        async def close(self) -> None:
            closed.set()

    async def _request() -> None:
        async with AsyncUnitOfWork():
            entered.set()
            await asyncio.sleep(30)

    with patch(
        "src.infra.database.uow_async.AsyncSessionLocal", return_value=_Session()
    ):
        request = asyncio.create_task(_request())
        await asyncio.wait_for(entered.wait(), timeout=1)
        request.cancel()
        with caplog.at_level(logging.WARNING):
            with pytest.raises(asyncio.CancelledError):
                await request
        await asyncio.wait_for(closed.wait(), timeout=1)

    assert closed.is_set()
    assert any(
        record.levelno == logging.WARNING
        and "Rollback failed; connection will be discarded" in record.getMessage()
        and record.exc_info is not None
        and isinstance(record.exc_info[1], RuntimeError)
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_cancelled_request_still_reports_a_real_close_failure(caplog):
    """A close error replaces the disconnect and is not logged a second time."""
    entered = asyncio.Event()

    class _Session:
        async def commit(self) -> None:
            raise AssertionError("a cancelled request must not commit")

        async def rollback(self) -> None:
            return None

        async def close(self) -> None:
            raise RuntimeError("close failed")

    async def _request() -> None:
        async with AsyncUnitOfWork():
            entered.set()
            await asyncio.sleep(30)

    with patch(
        "src.infra.database.uow_async.AsyncSessionLocal", return_value=_Session()
    ):
        request = asyncio.create_task(_request())
        await asyncio.wait_for(entered.wait(), timeout=1)
        request.cancel()
        with caplog.at_level(logging.ERROR):
            with pytest.raises(RuntimeError, match="close failed") as raised:
                await request

    assert isinstance(raised.value.__context__, asyncio.CancelledError)
    assert not any(
        "Async DB session close failed" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_commit_failure_still_rolls_back_and_propagates():
    events: list[str] = []

    class _Session:
        async def commit(self) -> None:
            events.append("commit")
            raise RuntimeError("commit failed")

        async def rollback(self) -> None:
            events.append("rollback")

        async def close(self) -> None:
            events.append("close")

    with patch(
        "src.infra.database.uow_async.AsyncSessionLocal", return_value=_Session()
    ):
        with pytest.raises(RuntimeError, match="commit failed"):
            async with AsyncUnitOfWork():
                pass

    assert events == ["commit", "rollback", "close"]


@pytest.mark.asyncio
async def test_slow_commit_failure_is_not_hidden_by_the_close_timeout(monkeypatch):
    """The close timeout must not turn a late commit failure into success.

    The commit stays on ``AsyncUnitOfWork.commit`` so the planner commit
    phase still runs, and it is not capped by the session-close timeout.
    """
    from contextlib import contextmanager

    import src.infra.database.config_async as cfg
    from src.planner_observability import planner_phase

    monkeypatch.setattr(cfg, "SESSION_CLOSE_TIMEOUT_SECONDS", 0.05)
    events: list[str] = []
    phases: list[str] = []

    @contextmanager
    def _record_phase(phase: str):
        phases.append(phase)
        with planner_phase(phase):
            yield

    monkeypatch.setattr("src.planner_observability.planner_phase", _record_phase)

    class _Session:
        async def commit(self) -> None:
            await asyncio.sleep(0.2)
            events.append("commit")
            raise RuntimeError("commit failed")

        async def rollback(self) -> None:
            events.append("rollback")

        async def close(self) -> None:
            events.append("close")

    with patch(
        "src.infra.database.uow_async.AsyncSessionLocal", return_value=_Session()
    ):
        with pytest.raises(RuntimeError, match="commit failed"):
            async with AsyncUnitOfWork():
                pass

    assert events == ["commit", "rollback", "close"]
    assert phases == ["commit"]


@pytest.mark.asyncio
async def test_async_uow_read_only_commit_raises():
    mock_session = AsyncMock()

    with patch("src.infra.database.uow_async.AsyncSessionLocal") as mock_factory:
        mock_factory.return_value = mock_session
        async with AsyncUnitOfWork(read_only=True) as uow:
            with pytest.raises(RuntimeError, match="Cannot commit a read-only"):
                await uow.commit()
