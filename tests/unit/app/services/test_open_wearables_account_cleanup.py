"""Deleting the Open Wearables user after the account deletion has committed."""

import logging
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.app.services.open_wearables_account_cleanup import (
    delete_open_wearables_user,
)

OW_USER_ID = "7d3f0c1e-2a4b-4c5d-8e9f-0a1b2c3d4e5f"


def _linked_uow(user_id) -> FakeUnitOfWork:
    uow = FakeUnitOfWork()
    uow.users.open_wearables_user_ids[str(user_id)] = OW_USER_ID
    return uow


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_had_user", [True, False])
async def test_confirmed_deletion_clears_the_link(platform_had_user):
    user_id = uuid4()
    uow = _linked_uow(user_id)
    wearable_accounts = MagicMock(delete_user=AsyncMock(return_value=platform_had_user))

    assert await delete_open_wearables_user(wearable_accounts, uow, user_id, OW_USER_ID)

    wearable_accounts.delete_user.assert_awaited_once_with(OW_USER_ID)
    assert str(user_id) not in uow.users.open_wearables_user_ids
    assert uow.committed is True


@pytest.mark.asyncio
async def test_platform_failure_keeps_the_link_and_logs_an_error(caplog):
    user_id = uuid4()
    uow = _linked_uow(user_id)
    wearable_accounts = MagicMock(
        delete_user=AsyncMock(side_effect=RuntimeError("unreachable"))
    )

    with caplog.at_level(logging.ERROR):
        cleared = await delete_open_wearables_user(
            wearable_accounts, uow, user_id, OW_USER_ID
        )

    assert cleared is False
    assert uow.users.open_wearables_user_ids[str(user_id)] == OW_USER_ID
    assert uow.committed is False
    assert "kept the link for retry" in caplog.text


@pytest.mark.asyncio
async def test_unconfigured_platform_keeps_the_link():
    user_id = uuid4()
    uow = _linked_uow(user_id)

    assert not await delete_open_wearables_user(None, uow, user_id, OW_USER_ID)
    assert uow.users.open_wearables_user_ids[str(user_id)] == OW_USER_ID


@pytest.mark.asyncio
async def test_failing_to_clear_the_link_reports_not_cleared(caplog):
    user_id = uuid4()
    uow = _linked_uow(user_id)
    uow.users.clear_open_wearables_user_id = AsyncMock(
        side_effect=RuntimeError("database unavailable")
    )
    wearable_accounts = MagicMock(delete_user=AsyncMock(return_value=True))

    with caplog.at_level(logging.ERROR):
        cleared = await delete_open_wearables_user(
            wearable_accounts, uow, user_id, OW_USER_ID
        )

    # A rerun finds the row and Open Wearables answers "not found", which clears it.
    assert cleared is False
    assert uow.rolled_back is True
    assert "link was not cleared" in caplog.text
