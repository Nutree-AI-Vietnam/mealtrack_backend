"""The Open Wearables link is read and stored inside a unit of work."""

from uuid import uuid4

import pytest
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.app.services.open_wearables_account_links import OpenWearablesAccountLinks


def _links(uow: FakeUnitOfWork, read_uow: FakeUnitOfWork | None = None):
    return OpenWearablesAccountLinks(lambda: uow, lambda: read_uow or uow)


@pytest.mark.asyncio
async def test_link_commits_through_the_unit_of_work():
    user_id = str(uuid4())
    uow = FakeUnitOfWork()

    stored = await _links(uow).link(user_id, "ow-1")

    assert stored == "ow-1"
    assert uow.users.open_wearables_user_ids[user_id] == "ow-1"
    assert uow.committed is True


@pytest.mark.asyncio
async def test_link_keeps_the_first_stored_id():
    user_id = str(uuid4())
    uow = FakeUnitOfWork()
    uow.users.open_wearables_user_ids[user_id] = "ow-first"

    stored = await _links(uow).link(user_id, "ow-second")

    assert stored == "ow-first"
    assert uow.users.open_wearables_user_ids[user_id] == "ow-first"


@pytest.mark.asyncio
async def test_linked_user_id_reads_in_a_read_only_unit_of_work():
    user_id = str(uuid4())
    write_uow = FakeUnitOfWork()
    read_uow = FakeUnitOfWork(read_only=True)
    read_uow.users.open_wearables_user_ids[user_id] = "ow-1"

    assert await _links(write_uow, read_uow).linked_user_id(user_id) == "ow-1"
    assert write_uow.committed is False
