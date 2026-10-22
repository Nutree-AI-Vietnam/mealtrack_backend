"""MoMo passes recover on Postgres when the usual path was missed.

Sign-in settles a payment whose IPN and status polls never arrived, and a
grant that stayed unconfirmed past its window's start never sends an end time
that is already over.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.integration.postgres.momo_pass_fixtures import (
    BUYER_EMAIL,
    ORDER_ID,
    PASS,
    URLS,
    USER_ID,
    FakeMomo,
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
from src.infra.services.momo_pass_service import MomoPassService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SECOND_ORDER_ID = "b" * 32


@pytest_asyncio.fixture
async def factory(migrated_database: str) -> AsyncIterator[async_sessionmaker]:
    async with momo_sessions(migrated_database) as sessions:
        yield sessions


async def _move_windows_back(sessions: async_sessionmaker, by: timedelta) -> None:
    """Age every reserved window, as if RevenueCat had been failing that long."""
    async with sessions() as db:
        await db.execute(
            update(MomoPaymentOrder).values(
                access_starts_at=MomoPaymentOrder.access_starts_at - by,
                access_ends_at=MomoPaymentOrder.access_ends_at - by,
            )
        )
        await db.commit()


async def _reserve_while_revenuecat_is_down(sessions, *order_ids: str) -> None:
    down = MomoPassEntitlements(SlowRevenueCat(ok=False))
    for order_id in order_ids:
        async with sessions() as db:
            assert not await down.grant(db, order_id, USER_ID)


@pytest.mark.parametrize(
    ("bought", "verified_email"),
    [
        (
            {"channel": "web_funnel", "user_id": None, "buyer_email": BUYER_EMAIL},
            BUYER_EMAIL.upper(),
        ),
        # The app lost the order id (reinstall or new device).
        ({"channel": "app"}, None),
    ],
    ids=["quiz-order", "in-app-order"],
)
async def test_sign_in_settles_a_payment_whose_ipn_and_polls_were_missed(
    factory, bought, verified_email
):
    await seed(factory, momo_order(**bought))
    momo, revenuecat = FakeMomo(result_code=0), SlowRevenueCat()
    service = MomoPassService(factory, momo, MomoPassEntitlements(revenuecat), URLS)

    assert await service.activate(USER_ID, verified_email)

    order = await load_order(factory)
    assert momo.queries == 1
    assert order.status == "paid" and order.granted_user_id == USER_ID
    assert order.granted_at is not None
    assert revenuecat.end_times == [ms(order.access_ends_at)]


async def test_sign_in_asks_about_every_recent_pending_order(factory):
    await seed(factory, momo_order(), momo_order(SECOND_ORDER_ID))
    momo, revenuecat = FakeMomo(result_code=0), SlowRevenueCat()
    service = MomoPassService(factory, momo, MomoPassEntitlements(revenuecat), URLS)

    assert await service.activate(USER_ID, None)

    first = await load_order(factory)
    second = await load_order(factory, SECOND_ORDER_ID)
    assert momo.queries == 2
    windows = sorted([(o.access_starts_at, o.access_ends_at) for o in (first, second)])
    assert windows[0][1] == windows[1][0]
    assert first.granted_at is not None and second.granted_at is not None


async def test_sign_in_leaves_old_and_other_buyers_pending_orders_alone(factory):
    await seed(
        factory,
        momo_order(created_at=utc_now() - timedelta(days=31)),
        momo_order(
            SECOND_ORDER_ID,
            channel="web_funnel",
            user_id=None,
            buyer_email="someone-else@example.test",
        ),
    )
    momo = FakeMomo(result_code=0)
    service = MomoPassService(
        factory, momo, MomoPassEntitlements(SlowRevenueCat()), URLS
    )

    assert not await service.activate(USER_ID, BUYER_EMAIL)

    assert momo.queries == 0


async def test_lapsed_reservation_is_granted_again_from_now(factory):
    await seed(factory, momo_order(status="paid"))
    await _reserve_while_revenuecat_is_down(factory, ORDER_ID)
    # RevenueCat stayed down, or nobody retried, for longer than the pass.
    await _move_windows_back(factory, timedelta(weeks=5))
    revenuecat = SlowRevenueCat()
    entitlements = MomoPassEntitlements(revenuecat)
    retried_at = utc_now()

    async with factory() as db:
        assert await entitlements.grant(db, ORDER_ID, USER_ID)
        assert await entitlements.has_active_pass(db, USER_ID)

    order = await load_order(factory)
    assert order.access_starts_at >= retried_at
    assert order.access_ends_at - order.access_starts_at == PASS
    assert revenuecat.end_times == [ms(order.access_ends_at)]


async def test_concurrent_retries_of_a_lapsed_reservation_grant_one_window(factory):
    await seed(factory, momo_order(status="paid"))
    await _reserve_while_revenuecat_is_down(factory, ORDER_ID)
    await _move_windows_back(factory, timedelta(weeks=5))
    revenuecat = SlowRevenueCat()
    entitlements = MomoPassEntitlements(revenuecat)

    async def retry() -> bool:
        async with factory() as db:
            return await entitlements.grant(db, ORDER_ID, USER_ID)

    assert all(await asyncio.gather(*(retry() for _ in range(4))))

    order = await load_order(factory)
    assert order.granted_at is not None and order.access_ends_at > utc_now()
    assert set(revenuecat.end_times) == {ms(order.access_ends_at)}


async def test_lapsed_reservation_revenuecat_already_holds_is_confirmed(factory):
    await seed(factory, momo_order(status="paid"))
    await _reserve_while_revenuecat_is_down(factory, ORDER_ID)
    # The grant landed upstream but its answer was lost.
    await _move_windows_back(factory, timedelta(hours=3))
    reserved = await load_order(factory)
    revenuecat = SlowRevenueCat(promotional_ends=[reserved.access_ends_at])

    async with factory() as db:
        assert await MomoPassEntitlements(revenuecat).grant(db, ORDER_ID, USER_ID)

    order = await load_order(factory)
    assert revenuecat.end_times == []
    assert order.granted_at is not None
    assert order.access_ends_at == reserved.access_ends_at


async def test_lapsed_reservations_are_granted_again_back_to_back(factory):
    paid_at = utc_now() - timedelta(weeks=5)
    await seed(
        factory,
        momo_order(status="paid", paid_at=paid_at),
        momo_order(SECOND_ORDER_ID, status="paid", paid_at=paid_at + PASS / 2),
    )
    await _reserve_while_revenuecat_is_down(factory, ORDER_ID, SECOND_ORDER_ID)
    # The second window has started but not ended: it lapsed as well.
    await _move_windows_back(factory, timedelta(weeks=5))
    revenuecat = SlowRevenueCat()
    retried_at = utc_now()

    async with factory() as db:
        await MomoPassEntitlements(revenuecat).activate_for_user(db, USER_ID, None)

    first = await load_order(factory)
    second = await load_order(factory, SECOND_ORDER_ID)
    assert first.access_starts_at >= retried_at
    assert second.access_starts_at == first.access_ends_at
    assert second.access_ends_at - first.access_starts_at == 2 * PASS
    assert revenuecat.end_times == [ms(first.access_ends_at), ms(second.access_ends_at)]
    assert first.granted_at is not None and second.granted_at is not None
