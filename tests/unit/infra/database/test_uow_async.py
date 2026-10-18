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


@pytest.mark.asyncio
async def test_async_uow_read_only_commit_raises():
    mock_session = AsyncMock()

    with patch("src.infra.database.uow_async.AsyncSessionLocal") as mock_factory:
        mock_factory.return_value = mock_session
        async with AsyncUnitOfWork(read_only=True) as uow:
            with pytest.raises(RuntimeError, match="Cannot commit a read-only"):
                await uow.commit()
