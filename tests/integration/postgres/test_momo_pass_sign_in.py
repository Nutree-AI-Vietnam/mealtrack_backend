"""Sign-in and app activate settle, credit and sync MoMo passes on Postgres.

A payment whose IPN and status polls never arrived still unlocks when its
buyer signs in or the app activates, and only that buyer's few newest pending
orders are asked about.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.integration.postgres.momo_pass_fixtures import (
    BUYER_EMAIL,
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

from src.api.dependencies.auth import get_current_user_id, verify_firebase_token
from src.api.routes.v1.momo_payments import router as momo_router
from src.domain.utils.timezone_utils import utc_now
from src.infra.services.momo_pass_entitlement import MomoPassEntitlements
from src.infra.services.momo_pass_service import MomoPassService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SECOND_ORDER_ID = "b" * 32


@pytest_asyncio.fixture
async def factory(migrated_database: str) -> AsyncIterator[async_sessionmaker]:
    async with momo_sessions(migrated_database) as sessions:
        yield sessions


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


async def test_activation_asks_momo_even_while_a_status_poll_holds_the_slot(factory):
    # A funnel poll has just claimed the query slot; it could not credit this
    # ownerless quiz order, so activation must ask MoMo itself.
    await seed(
        factory,
        momo_order(
            channel="web_funnel",
            user_id=None,
            buyer_email=BUYER_EMAIL,
            last_checked_at=utc_now(),
        ),
    )
    momo, revenuecat = FakeMomo(result_code=0), SlowRevenueCat()
    service = MomoPassService(factory, momo, MomoPassEntitlements(revenuecat), URLS)

    assert await service.activate(USER_ID, BUYER_EMAIL)

    order = await load_order(factory)
    assert momo.queries == 1
    assert order.status == "paid" and order.granted_user_id == USER_ID
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
    assert revenuecat.end_times == [ms(windows[1][1])]


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


async def test_sign_in_retries_a_pass_credited_while_revenuecat_was_down(factory):
    await seed(factory, momo_order(status="paid"))
    down = SlowRevenueCat(ok=False)
    service = MomoPassService(factory, FakeMomo(), MomoPassEntitlements(down), URLS)
    assert not await service.activate(USER_ID, None)
    revenuecat = SlowRevenueCat()
    service = MomoPassService(
        factory, FakeMomo(), MomoPassEntitlements(revenuecat), URLS
    )

    assert await service.activate(USER_ID, None)

    order = await load_order(factory)
    assert revenuecat.end_times == down.end_times == [ms(order.access_ends_at)]
    assert order.granted_at is not None


async def test_app_activate_unlocks_a_quiz_pass_paid_while_signed_in(
    factory, monkeypatch
):
    # The buyer was signed in when they paid on the quiz, and the IPN was lost.
    await seed(
        factory,
        momo_order(channel="web_funnel", user_id=None, buyer_email=BUYER_EMAIL),
    )
    momo, revenuecat = FakeMomo(result_code=0), SlowRevenueCat()
    service = MomoPassService(factory, momo, MomoPassEntitlements(revenuecat), URLS)
    monkeypatch.setattr(
        "src.bootstrap.momo_payments.get_momo_pass_service", lambda: service
    )
    app = FastAPI()
    app.include_router(momo_router)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[verify_firebase_token] = lambda: {
        "email": BUYER_EMAIL,
        "email_verified": True,
        "firebase": {"sign_in_provider": "apple.com"},
    }
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.post("/v1/payments/momo/activate")

    order = await load_order(factory)
    assert response.status_code == 200
    assert response.json() == {"has_pass": True}
    assert momo.queries == 1
    assert (order.status, order.granted_user_id) == ("paid", USER_ID)
    assert order.granted_at is not None
    assert revenuecat.end_ms == ms(order.access_ends_at)
