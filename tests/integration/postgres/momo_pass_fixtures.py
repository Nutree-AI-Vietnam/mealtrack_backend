"""Seed data and fakes for the MoMo pass tests on Postgres.

MoMo and RevenueCat are fakes; no real service is called.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.domain.utils.timezone_utils import utc_now
from src.infra.adapters.momo_gateway_adapter import MomoCredentials, MomoGatewayError
from src.infra.database.models.momo_payment_order import MomoPaymentOrder
from src.infra.database.models.user.user import User
from src.infra.services.momo_pass_entitlement import MomoPassEntitlements
from src.infra.services.momo_pass_orders import find_order
from src.infra.services.momo_pass_service import MomoUrls

USER_ID = "00000000-0000-4000-8000-000000000001"
FIREBASE_UID = "momo-firebase-uid"
BUYER_EMAIL = "momo-buyer@example.test"
ORDER_ID = "a" * 32
AMOUNT = 99_000
PASS = timedelta(weeks=4)
CREDENTIALS = MomoCredentials("sandbox", "NUTREE", "access", "secret")
URLS = MomoUrls("https://api.example/ipn", "https://quiz.example/r", "nutree://r")
_OPEN_TRANSACTIONS = text(
    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
    "AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'"
)


@asynccontextmanager
async def momo_sessions(database_url: str) -> AsyncIterator[async_sessionmaker]:
    """Sessions configured like the API's (objects survive commits), clean tables."""
    engine = create_async_engine(database_url, pool_size=10)
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
            text(
                "TRUNCATE TABLE momo_payment_orders, web_funnel_leads, users "
                "RESTART IDENTITY CASCADE"
            )
        )
        await db.commit()


def momo_order(order_id: str = ORDER_ID, **values) -> MomoPaymentOrder:
    status = values.get("status", "pending")
    return MomoPaymentOrder(
        **{
            "order_id": order_id,
            "request_id": order_id[::-1],
            "channel": "app",
            "request_type": "captureWallet",
            "plan": "monthly",
            "weeks": 4,
            "amount": AMOUNT,
            "user_id": USER_ID,
            "status": status,
            "paid_at": utc_now() if status == "paid" else None,
            **values,
        }
    )


async def seed(sessions: async_sessionmaker, *orders: MomoPaymentOrder) -> None:
    async with sessions() as db:
        db.add(
            User(
                id=USER_ID,
                firebase_uid=FIREBASE_UID,
                email=BUYER_EMAIL,
                username="momo-buyer",
                password_hash="test",
            )
        )
        await db.flush()
        db.add_all(orders)
        await db.commit()


async def load_order(
    sessions: async_sessionmaker, order_id: str = ORDER_ID
) -> MomoPaymentOrder:
    async with sessions() as db:
        order = await find_order(db, order_id)
        assert order is not None
        return order


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


async def open_transactions(sessions: async_sessionmaker) -> int:
    async with sessions() as db:
        return int(await db.scalar(_OPEN_TRANSACTIONS))


class SlowRevenueCat:
    """Grants slowly; ``ok=False`` is a failed grant or a lost answer."""

    def __init__(self, sessions=None, ok=True):
        self.sessions, self.ok = sessions, ok
        self.end_times: list[int] = []
        self.open_transactions: list[int] = []

    async def grant_promotional_entitlement(self, app_user_id, _entitlement, end_ms):
        assert app_user_id == FIREBASE_UID
        self.end_times.append(end_ms)
        if self.sessions:
            self.open_transactions.append(await open_transactions(self.sessions))
        await asyncio.sleep(0.2)
        return self.ok


async def deliver(
    sessions: async_sessionmaker, revenuecat, order_id: str = ORDER_ID
) -> bool:
    """Credit one order to the seeded user, then sync them, as a status poll does."""
    entitlements = MomoPassEntitlements(revenuecat)
    async with sessions() as db:
        await entitlements.credit(db, order_id, USER_ID)
        return await entitlements.sync(db, USER_ID)


async def move_windows_back(sessions: async_sessionmaker, by: timedelta) -> None:
    """Age every credited window, as if that much time had passed."""
    async with sessions() as db:
        await db.execute(
            update(MomoPaymentOrder).values(
                access_starts_at=MomoPaymentOrder.access_starts_at - by,
                access_ends_at=MomoPaymentOrder.access_ends_at - by,
            )
        )
        await db.commit()


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
            self.open_transactions.append(await open_transactions(self.sessions))
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
