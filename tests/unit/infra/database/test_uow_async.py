"""Unit tests for AsyncUnitOfWork."""

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
async def test_async_uow_read_only_commit_raises():
    mock_session = AsyncMock()

    with patch("src.infra.database.uow_async.AsyncSessionLocal") as mock_factory:
        mock_factory.return_value = mock_session
        async with AsyncUnitOfWork(read_only=True) as uow:
            with pytest.raises(RuntimeError, match="Cannot commit a read-only"):
                await uow.commit()
