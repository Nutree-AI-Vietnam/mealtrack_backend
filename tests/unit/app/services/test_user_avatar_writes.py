"""After-commit avatar image deletion retries, then leaves an error log."""

import logging
from unittest.mock import AsyncMock

import pytest

from src.app.services.user_avatar_writes import (
    AVATAR_IMAGE_DELETE_ATTEMPTS,
    delete_avatar_image,
)


@pytest.mark.asyncio
async def test_delete_succeeds_on_first_attempt():
    store = AsyncMock()
    store.delete_async = AsyncMock(return_value=True)

    assert await delete_avatar_image(store, "img-1", reason="avatar replaced")

    store.delete_async.assert_awaited_once_with("img-1")


@pytest.mark.asyncio
async def test_failed_delete_is_retried_until_it_succeeds():
    store = AsyncMock()
    store.delete_async = AsyncMock(side_effect=[False, RuntimeError("timeout"), True])

    deleted = await delete_avatar_image(
        store, "img-2", reason="avatar replaced", retry_seconds=0
    )

    assert deleted is True
    assert store.delete_async.await_count == 3


@pytest.mark.asyncio
async def test_delete_that_keeps_failing_logs_the_image_id(caplog):
    store = AsyncMock()
    store.delete_async = AsyncMock(return_value=False)

    with caplog.at_level(logging.WARNING, logger="src.app.services.user_avatar_writes"):
        deleted = await delete_avatar_image(
            store, "img-3", reason="account deleted", retry_seconds=0
        )

    assert deleted is False
    assert store.delete_async.await_count == AVATAR_IMAGE_DELETE_ATTEMPTS == 3
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "img-3" in errors[0].getMessage()
    assert "account deleted" in errors[0].getMessage()


@pytest.mark.asyncio
async def test_missing_image_store_logs_the_image_id(caplog):
    with caplog.at_level(logging.ERROR, logger="src.app.services.user_avatar_writes"):
        deleted = await delete_avatar_image(None, "img-4", reason="account deleted")

    assert deleted is False
    assert any("img-4" in r.getMessage() for r in caplog.records)
