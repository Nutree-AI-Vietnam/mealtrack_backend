"""One user's RevenueCat grants land in order, so a replaced end is the latest.

RevenueCat may replace a custom promotional instead of keeping the latest
expiry. The fake here does replace, and its first grant is the slowest, so an
earlier end sent alongside a later one would land last. Postgres is real;
MoMo and RevenueCat are fakes.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.integration.postgres.momo_pass_fixtures import (
    ORDER_ID,
    PASS,
    URLS,
    USER_ID,
    FakeMomo,
    SlowRevenueCat,
    deliver,
    load_order,
    momo_order,
    momo_sessions,
    ms,
    seed,
)

from src.infra.services.momo_pass_entitlement import MomoPassEntitlements
from src.infra.services.momo_pass_orders import find_order
from src.infra.services.momo_pass_service import MomoPassService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

ORDER_IDS = [ORDER_ID, "b" * 32, "c" * 32]


@pytest_asyncio.fixture
async def factory(migrated_database: str) -> AsyncIterator[async_sessionmaker]:
    async with momo_sessions(migrated_database) as sessions:
        yield sessions


async def _seed_paid(sessions: async_sessionmaker, count: int) -> None:
    await seed(sessions, *(momo_order(i, status="paid") for i in ORDER_IDS[:count]))


async def test_concurrent_syncs_leave_revenuecat_holding_the_latest_end(factory):
    await _seed_paid(factory, 3)
    revenuecat = SlowRevenueCat(delays=(0.3, 0.05))
    entitlements = MomoPassEntitlements(revenuecat)
    loaded = asyncio.Barrier(len(ORDER_IDS))

    async def deliver_one(order_id: str) -> bool:
        async with factory() as db:
            # Hold a connection, so every credit reaches the database at once.
            held = await find_order(db, order_id)
            assert held is not None
            await loaded.wait()
            await entitlements.credit(db, held.order_id, USER_ID)
            return await entitlements.sync(db, USER_ID)

    results = await asyncio.gather(*(deliver_one(i) for i in ORDER_IDS))

    orders = [await load_order(factory, order_id) for order_id in ORDER_IDS]
    latest_end = max(order.access_ends_at for order in orders)
    assert all(results)
    assert revenuecat.most_in_flight == 1
    assert revenuecat.end_times == sorted(revenuecat.end_times)
    assert revenuecat.end_ms == ms(latest_end)
    assert all(order.granted_at is not None for order in orders)


async def test_credit_committed_while_the_lock_is_held_is_sent_by_its_holder(
    factory,
):
    await _seed_paid(factory, 2)
    revenuecat = SlowRevenueCat(delays=(0.3,))
    holder = asyncio.create_task(deliver(factory, revenuecat))
    await revenuecat.called.wait()

    # Crediting never waits on RevenueCat; the sync leaves the send to the holder.
    assert await deliver(factory, revenuecat, ORDER_IDS[1])
    assert (revenuecat.in_flight, len(revenuecat.end_times)) == (1, 1)
    assert await holder

    first, second = [await load_order(factory, i) for i in ORDER_IDS[:2]]
    assert second.access_starts_at == first.access_ends_at
    assert revenuecat.end_times == [ms(first.access_ends_at), ms(second.access_ends_at)]
    assert revenuecat.end_ms == ms(second.access_ends_at)
    assert first.granted_at is not None and second.granted_at is not None


async def test_failed_grant_leaves_its_orders_unsent_until_the_next_activate(
    factory,
):
    await _seed_paid(factory, 2)
    # The holder's second pass, for the credit made meanwhile, fails.
    revenuecat = SlowRevenueCat(delays=(0.3, 0.05), fail_calls=(2,))
    holder = asyncio.create_task(deliver(factory, revenuecat))
    await revenuecat.called.wait()
    assert await deliver(factory, revenuecat, ORDER_IDS[1])
    assert not await holder

    first, second = [await load_order(factory, i) for i in ORDER_IDS[:2]]
    assert first.granted_at is not None and second.granted_at is None
    assert revenuecat.end_ms == ms(first.access_ends_at)

    service = MomoPassService(
        factory, FakeMomo(), MomoPassEntitlements(revenuecat), URLS
    )
    assert await service.activate(USER_ID, None)

    second = await load_order(factory, ORDER_IDS[1])
    assert second.granted_at is not None
    assert second.access_ends_at - first.access_ends_at == PASS
    assert revenuecat.end_times[-2:] == [ms(second.access_ends_at)] * 2
    assert revenuecat.end_ms == ms(second.access_ends_at)
