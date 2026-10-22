"""MoMo passes under real row locks: one grant per order, one MoMo query per slot.

Concurrent grants, an IPN racing a status poll, and concurrent polls run
against Postgres. MoMo and RevenueCat are fakes; no real service is called.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.domain.model.payments.momo_signature import RESULT_FIELDS, momo_signature
from src.domain.utils.timezone_utils import utc_now
from src.infra.adapters.momo_gateway_adapter import MomoCredentials, MomoGatewayError
from src.infra.database.models.momo_payment_order import MomoPaymentOrder
from src.infra.database.models.user.user import User
from src.infra.services.momo_pass_entitlement import MomoPassEntitlements
from src.infra.services.momo_pass_orders import find_order
from src.infra.services.momo_pass_service import MomoPassService, MomoUrls

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

USER_ID = "00000000-0000-4000-8000-000000000001"
FIREBASE_UID = "momo-firebase-uid"
ORDER_ID = "a" * 32
AMOUNT = 99_000
PASS = timedelta(weeks=4)
CREDENTIALS = MomoCredentials("sandbox", "NUTREE", "access", "secret")
URLS = MomoUrls("https://api.example/ipn", "https://quiz.example/r", "nutree://r")
_OPEN_TRANSACTIONS = text(
    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
    "AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'"
)


@pytest_asyncio.fixture
async def factory(migrated_database: str) -> AsyncIterator[async_sessionmaker]:
    """Sessions configured like the API's: objects survive commits."""
    engine = create_async_engine(migrated_database, pool_size=10)
    sessions = async_sessionmaker(
        bind=engine, class_=AsyncSession, autoflush=False, expire_on_commit=False
    )
    await _truncate(sessions)
    try:
        yield sessions
    finally:
        await _truncate(sessions)
        await engine.dispose()


async def _truncate(sessions: async_sessionmaker) -> None:
    async with sessions() as db:
        await db.execute(
            text("TRUNCATE TABLE momo_payment_orders, users RESTART IDENTITY CASCADE")
        )
        await db.commit()


async def _seed(sessions: async_sessionmaker, status: str) -> None:
    async with sessions() as db:
        db.add(
            User(
                id=USER_ID,
                firebase_uid=FIREBASE_UID,
                email="momo-buyer@example.test",
                username="momo-buyer",
                password_hash="test",
            )
        )
        await db.flush()
        db.add(
            MomoPaymentOrder(
                order_id=ORDER_ID,
                request_id="r" * 32,
                channel="app",
                request_type="captureWallet",
                plan="monthly",
                weeks=4,
                amount=AMOUNT,
                user_id=USER_ID,
                status=status,
                paid_at=utc_now() if status == "paid" else None,
            )
        )
        await db.commit()


async def _order(sessions: async_sessionmaker) -> MomoPaymentOrder:
    async with sessions() as db:
        order = await find_order(db, ORDER_ID)
        assert order is not None
        return order


def _ms(moment) -> int:
    return int(moment.timestamp() * 1000)


class SlowRevenueCat:
    def __init__(self, sessions: async_sessionmaker | None = None):
        self.sessions, self.end_times, self.open_transactions = sessions, [], []

    async def grant_promotional_entitlement(self, app_user_id, _entitlement, end_ms):
        assert app_user_id == FIREBASE_UID
        self.end_times.append(end_ms)
        if self.sessions:
            self.open_transactions.append(await _open_transactions(self.sessions))
        await asyncio.sleep(0.2)
        return True


class FakeMomo:
    credentials = CREDENTIALS

    def __init__(self, result_code=0, trans_id=222, fails=False, sessions=None):
        self.result_code, self.trans_id, self.fails = result_code, trans_id, fails
        self.sessions, self.queries, self.open_transactions = sessions, 0, []
        self.asked = asyncio.Event()

    async def query_payment(self, *, order_id, request_id):
        self.queries += 1
        self.asked.set()
        if self.sessions:
            self.open_transactions.append(await _open_transactions(self.sessions))
        await asyncio.sleep(0.2)
        if self.fails:
            raise MomoGatewayError("MoMo is unreachable")
        return {
            "orderId": order_id,
            "resultCode": self.result_code,
            "amount": AMOUNT,
            "transId": self.trans_id,
            "payType": "qr",
            "message": "ok",
        }


async def _open_transactions(sessions: async_sessionmaker) -> int:
    async with sessions() as db:
        return int(await db.scalar(_OPEN_TRANSACTIONS))


def _paid_ipn(trans_id: int) -> dict:
    body = {
        "partnerCode": "NUTREE",
        "orderId": ORDER_ID,
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


async def test_concurrent_grants_leave_one_window_with_one_end_time(factory):
    await _seed(factory, status="paid")
    revenuecat = SlowRevenueCat()
    entitlements = MomoPassEntitlements(revenuecat)
    grants = 6
    loaded = asyncio.Barrier(grants)
    started = utc_now()

    async def grant() -> bool:
        async with factory() as db:
            # Keep the ungranted order in the identity map (a weak map, so hold
            # a reference) while granting, as status polls and IPNs do.
            held = await find_order(db, ORDER_ID)
            assert held is not None
            await loaded.wait()
            return await entitlements.grant(db, held.order_id, held.user_id)

    results = await asyncio.gather(*(grant() for _ in range(grants)))

    order = await _order(factory)
    assert all(results)
    assert order.granted_at is not None and order.granted_user_id == USER_ID
    assert order.access_starts_at - started < timedelta(minutes=1)
    assert order.access_ends_at - order.access_starts_at == PASS
    assert revenuecat.end_times
    assert set(revenuecat.end_times) == {_ms(order.access_ends_at)}


async def test_paid_ipn_racing_a_status_poll_settles_once_and_grants_once(factory):
    await _seed(factory, status="pending")
    momo, revenuecat = FakeMomo(trans_id=222), SlowRevenueCat()
    service = MomoPassService(factory, momo, MomoPassEntitlements(revenuecat), URLS)

    # The poll has read the pending order and is waiting on MoMo's answer.
    poll = asyncio.create_task(service.app_order(ORDER_ID, USER_ID))
    await momo.asked.wait()
    assert await service.handle_ipn(_paid_ipn(trans_id=111))
    polled = await poll

    order = await _order(factory)
    assert polled is not None and polled["status"] == "paid"
    # The poll's later paid result (transId 222) did not settle the order again.
    assert (order.status, order.trans_id) == ("paid", 111)
    assert order.granted_at is not None
    assert order.access_ends_at - order.access_starts_at == PASS
    assert set(revenuecat.end_times) == {_ms(order.access_ends_at)}


@pytest.mark.parametrize("momo_fails", [False, True], ids=["pending", "momo-down"])
async def test_concurrent_status_polls_ask_momo_once(factory, momo_fails):
    await _seed(factory, status="pending")
    momo = FakeMomo(result_code=1000, fails=momo_fails)
    service = MomoPassService(
        factory, momo, MomoPassEntitlements(SlowRevenueCat()), URLS
    )

    polled = await asyncio.gather(
        *(service.app_order(ORDER_ID, USER_ID) for _ in range(6))
    )

    assert momo.queries == 1
    assert [result["status"] for result in polled] == ["pending"] * 6
    assert (await _order(factory)).last_checked_at is not None


async def test_momo_and_revenuecat_calls_run_outside_any_transaction(factory):
    await _seed(factory, status="pending")
    momo, revenuecat = FakeMomo(sessions=factory), SlowRevenueCat(sessions=factory)
    service = MomoPassService(factory, momo, MomoPassEntitlements(revenuecat), URLS)

    polled = await service.app_order(ORDER_ID, USER_ID)

    assert polled is not None and polled["status"] == "paid"
    assert momo.open_transactions == [0]
    assert revenuecat.open_transactions == [0]


async def test_a_reserved_window_is_not_an_active_pass_until_granted(factory):
    await _seed(factory, status="paid")

    class DownRevenueCat(SlowRevenueCat):
        async def grant_promotional_entitlement(self, *args):
            await super().grant_promotional_entitlement(*args)
            return False

    down = DownRevenueCat()
    async with factory() as db:
        assert not await MomoPassEntitlements(down).grant(db, ORDER_ID, USER_ID)
        assert not await MomoPassEntitlements(down).has_active_pass(db, USER_ID)

    retry = SlowRevenueCat()
    async with factory() as db:
        assert await MomoPassEntitlements(retry).grant(db, ORDER_ID, USER_ID)
        assert await MomoPassEntitlements(retry).has_active_pass(db, USER_ID)
    assert retry.end_times == down.end_times
