"""Each paid MoMo pass week reaches RevenueCat once, even after failed grants.

RevenueCat promotional grants start when they are made, so a buyer's access
runs from a grant to the latest end sent. These tests measure that access,
not how windows stack relative to each other.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.integration.postgres.momo_pass_fixtures import (
    ORDER_ID,
    PASS,
    USER_ID,
    SlowRevenueCat,
    load_order,
    momo_order,
    momo_sessions,
    ms,
    seed,
)

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.momo_payment_order import MomoPaymentOrder
from src.infra.services.momo_pass_entitlement import MomoPassEntitlements

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SECOND_ORDER_ID = "b" * 32
MINUTE = timedelta(minutes=1)


@pytest_asyncio.fixture
async def factory(migrated_database: str) -> AsyncIterator[async_sessionmaker]:
    async with momo_sessions(migrated_database) as sessions:
        await seed(
            sessions,
            momo_order(status="paid"),
            momo_order(SECOND_ORDER_ID, status="paid"),
        )
        yield sessions


async def _grant(sessions, revenuecat, order_id: str) -> bool:
    async with sessions() as db:
        return await MomoPassEntitlements(revenuecat).grant(db, order_id, USER_ID)


async def _move_windows_back(sessions: async_sessionmaker, by: timedelta) -> None:
    async with sessions() as db:
        await db.execute(
            update(MomoPaymentOrder).values(
                access_starts_at=MomoPaymentOrder.access_starts_at - by,
                access_ends_at=MomoPaymentOrder.access_ends_at - by,
            )
        )
        await db.commit()


def _latest_end(*revenuecats: SlowRevenueCat) -> datetime:
    end_ms = max(end for fake in revenuecats for end in fake.end_times)
    return datetime.fromtimestamp(end_ms / 1000, UTC)


async def test_window_a_later_grant_delivered_is_never_granted_again(factory):
    assert not await _grant(factory, SlowRevenueCat(ok=False), ORDER_ID)
    # Minutes later the buyer pays again. That grant lands and, starting at
    # once, runs through the first window as well.
    revenuecat = SlowRevenueCat()
    assert await _grant(factory, revenuecat, SECOND_ORDER_ID)
    granted_until = _latest_end(revenuecat)
    # The first order is retried at the next sign-in, hours later.
    await _move_windows_back(factory, timedelta(hours=3))

    assert await _grant(factory, revenuecat, ORDER_ID)

    first = await load_order(factory)
    second = await load_order(factory, SECOND_ORDER_ID)
    assert first.granted_at is not None
    assert second.access_ends_at - first.access_starts_at == 2 * PASS
    assert len(revenuecat.end_times) == 1
    assert _latest_end(revenuecat) == granted_until


@pytest.mark.parametrize(
    "retry_order",
    [(ORDER_ID, SECOND_ORDER_ID), (SECOND_ORDER_ID, ORDER_ID)],
    ids=["first-order-first", "second-order-first"],
)
async def test_passes_reserved_in_one_outage_grant_their_weeks_once(
    factory, retry_order
):
    down = SlowRevenueCat(ok=False)
    for order_id in (ORDER_ID, SECOND_ORDER_ID):
        assert not await _grant(factory, down, order_id)
    # A week later the first window has lapsed; the second, stacked on it,
    # has not started yet.
    await _move_windows_back(factory, timedelta(weeks=1))
    revenuecat = SlowRevenueCat()
    retried_at = utc_now()

    for order_id in retry_order:
        assert await _grant(factory, revenuecat, order_id)

    first = await load_order(factory)
    second = await load_order(factory, SECOND_ORDER_ID)
    assert first.granted_at is not None and second.granted_at is not None
    assert revenuecat.end_times == sorted(revenuecat.end_times)
    access = _latest_end(revenuecat) - retried_at
    assert 2 * PASS - MINUTE <= access <= 2 * PASS + MINUTE


async def test_order_paid_after_a_lost_grant_answer_stacks_after_that_grant(factory):
    # RevenueCat applies the first grant, but its answer times out.
    lost = SlowRevenueCat(ok=False, lands=True)
    assert not await _grant(factory, lost, ORDER_ID)
    await _move_windows_back(factory, timedelta(hours=3))
    landed = await load_order(factory)
    revenuecat = SlowRevenueCat(promotional_ends=[landed.access_ends_at])

    assert await _grant(factory, revenuecat, SECOND_ORDER_ID)

    first = await load_order(factory)
    second = await load_order(factory, SECOND_ORDER_ID)
    assert first.granted_at is not None
    assert first.access_ends_at == landed.access_ends_at
    assert revenuecat.end_times == [ms(landed.access_ends_at + PASS)]
    assert second.access_ends_at == landed.access_ends_at + PASS


async def test_order_paid_after_a_failed_grant_brings_the_lapsed_pass_with_it(
    factory,
):
    assert not await _grant(factory, SlowRevenueCat(ok=False), ORDER_ID)
    await _move_windows_back(factory, timedelta(hours=3))
    revenuecat = SlowRevenueCat()
    paid_at = utc_now()

    assert await _grant(factory, revenuecat, SECOND_ORDER_ID)

    first = await load_order(factory)
    second = await load_order(factory, SECOND_ORDER_ID)
    assert first.granted_at is not None and second.granted_at is not None
    assert abs(first.access_starts_at - paid_at) < MINUTE
    assert second.access_starts_at == first.access_ends_at
    assert revenuecat.end_times == [ms(second.access_ends_at)]
