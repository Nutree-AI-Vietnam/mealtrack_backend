"""MoMo passes under real locks: one window per order, one MoMo query per slot.

Concurrent deliveries, concurrent IPNs, an IPN racing a status poll, and
concurrent polls run against Postgres. MoMo and RevenueCat are fakes; no real
service is called.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.integration.postgres.momo_pass_fixtures import (
    AMOUNT,
    CREDENTIALS,
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

from src.domain.model.payments.momo_signature import RESULT_FIELDS, momo_signature
from src.domain.utils.timezone_utils import utc_now
from src.infra.services.momo_pass_entitlement import MomoPassEntitlements
from src.infra.services.momo_pass_orders import find_order
from src.infra.services.momo_pass_service import MomoPassService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SECOND_ORDER_ID = "b" * 32


@pytest_asyncio.fixture
async def factory(migrated_database: str) -> AsyncIterator[async_sessionmaker]:
    async with momo_sessions(migrated_database) as sessions:
        yield sessions


def _paid_ipn(order_id: str = ORDER_ID, trans_id: int = 111) -> dict:
    body = {
        "partnerCode": "NUTREE",
        "orderId": order_id,
        "requestId": "r" * 32,
        "amount": AMOUNT,
        "orderInfo": "Nutree Premium 4 tuần",
        "orderType": "momo_wallet",
        "transId": trans_id,
        "resultCode": 0,
        "message": "ok",
        "payType": "qr",
        "responseTime": 1792682356885,
        "extraData": "",
    }
    signed = {**body, "accessKey": CREDENTIALS.access_key}
    body["signature"] = momo_signature(CREDENTIALS.secret_key, signed, RESULT_FIELDS)
    return body


async def test_concurrent_deliveries_credit_one_window_and_send_its_end(factory):
    await seed(factory, momo_order(status="paid"))
    revenuecat = SlowRevenueCat()
    entitlements = MomoPassEntitlements(revenuecat)
    deliveries = 6
    loaded = asyncio.Barrier(deliveries)
    started = utc_now()

    async def deliver() -> bool:
        async with factory() as db:
            # Keep the uncredited order in the identity map (a weak map, so
            # hold a reference) while crediting, as status polls and IPNs do.
            held = await find_order(db, ORDER_ID)
            assert held is not None
            await loaded.wait()
            await entitlements.credit(db, held.order_id, USER_ID)
            return await entitlements.sync(db, USER_ID)

    results = await asyncio.gather(*(deliver() for _ in range(deliveries)))

    order = await load_order(factory)
    assert all(results)
    assert order.granted_at is not None and order.granted_user_id == USER_ID
    assert order.access_starts_at - started < timedelta(minutes=1)
    assert order.access_ends_at - order.access_starts_at == PASS
    assert revenuecat.end_times
    assert set(revenuecat.end_times) == {ms(order.access_ends_at)}


async def test_orders_paid_concurrently_stack_back_to_back(factory):
    await seed(
        factory, momo_order(status="paid"), momo_order(SECOND_ORDER_ID, status="paid")
    )
    revenuecat = SlowRevenueCat()
    entitlements = MomoPassEntitlements(revenuecat)
    order_ids = [ORDER_ID, SECOND_ORDER_ID] * 2
    loaded = asyncio.Barrier(len(order_ids))
    started = utc_now()

    async def deliver(order_id: str) -> bool:
        async with factory() as db:
            # Hold a connection, so every credit reaches the database at once.
            held = await find_order(db, order_id)
            assert held is not None
            await loaded.wait()
            await entitlements.credit(db, held.order_id, USER_ID)
            return await entitlements.sync(db, USER_ID)

    results = await asyncio.gather(*(deliver(order_id) for order_id in order_ids))

    first, second = sorted(
        [await load_order(factory), await load_order(factory, SECOND_ORDER_ID)],
        key=lambda order: order.access_starts_at,
    )
    assert all(results)
    assert first.access_starts_at - started < timedelta(minutes=1)
    assert second.access_starts_at == first.access_ends_at
    assert second.access_ends_at - first.access_starts_at == 2 * PASS
    assert first.granted_at is not None and second.granted_at is not None
    assert max(revenuecat.end_times) == ms(second.access_ends_at)


async def test_paid_ipn_racing_a_status_poll_settles_once_and_grants_once(factory):
    await seed(factory, momo_order())
    momo, revenuecat = FakeMomo(trans_id=222), SlowRevenueCat()
    service = MomoPassService(factory, momo, MomoPassEntitlements(revenuecat), URLS)

    # The poll has read the pending order and is waiting on MoMo's answer.
    poll = asyncio.create_task(service.app_order(ORDER_ID, USER_ID))
    await momo.asked.wait()
    assert await service.handle_ipn(_paid_ipn(trans_id=111))
    polled = await poll

    order = await load_order(factory)
    assert polled is not None and polled["status"] == "paid"
    # The poll's later paid result (transId 222) did not settle the order again.
    assert (order.status, order.trans_id) == ("paid", 111)
    assert order.granted_at is not None
    assert order.access_ends_at - order.access_starts_at == PASS
    assert set(revenuecat.end_times) == {ms(order.access_ends_at)}


@pytest.mark.parametrize("momo_fails", [False, True], ids=["pending", "momo-down"])
async def test_concurrent_status_polls_ask_momo_once(factory, momo_fails):
    await seed(factory, momo_order())
    momo = FakeMomo(result_code=1000, fails=momo_fails)
    service = MomoPassService(
        factory, momo, MomoPassEntitlements(SlowRevenueCat()), URLS
    )

    polled = await asyncio.gather(
        *(service.app_order(ORDER_ID, USER_ID) for _ in range(6))
    )

    assert momo.queries == 1
    assert [result["status"] for result in polled] == ["pending"] * 6
    assert (await load_order(factory)).last_checked_at is not None


async def test_remote_calls_hold_no_row_locks(factory):
    await seed(factory, momo_order())
    momo, revenuecat = FakeMomo(sessions=factory), SlowRevenueCat(sessions=factory)
    service = MomoPassService(factory, momo, MomoPassEntitlements(revenuecat), URLS)

    polled = await service.app_order(ORDER_ID, USER_ID)

    assert polled is not None and polled["status"] == "paid"
    # MoMo is asked with no transaction open.
    assert momo.open_transactions == [(0, 0)]
    # RevenueCat only inside the sync lock holder's read transaction, which has
    # written and locked no row.
    assert revenuecat.open_transactions == [(1, 0)]
