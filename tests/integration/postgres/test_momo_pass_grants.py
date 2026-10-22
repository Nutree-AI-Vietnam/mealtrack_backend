"""Granting one MoMo pass on Postgres: stacking, ownership and the quiz lead."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.integration.postgres.momo_pass_fixtures import (
    BUYER_EMAIL,
    FIREBASE_UID,
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
from src.infra.database.models.user.user import User
from src.infra.database.models.web_funnel_claim import WebFunnelLead
from src.infra.services.momo_pass_entitlement import MomoPassEntitlements

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

OTHER_USER_ID = "00000000-0000-4000-8000-000000000002"


@pytest_asyncio.fixture
async def factory(migrated_database: str) -> AsyncIterator[async_sessionmaker]:
    async with momo_sessions(migrated_database) as sessions:
        yield sessions


async def _grant(sessions, revenuecat, order_id: str = ORDER_ID) -> bool:
    async with sessions() as db:
        return await MomoPassEntitlements(revenuecat).grant(db, order_id, USER_ID)


async def test_pass_bought_during_a_running_pass_starts_when_it_ends(factory):
    running_until = utc_now() + timedelta(days=5)
    await seed(
        factory,
        momo_order(
            "c" * 32,
            status="paid",
            granted_user_id=USER_ID,
            access_starts_at=running_until - PASS,
            access_ends_at=running_until,
            granted_at=running_until - PASS,
        ),
        momo_order(status="paid"),
    )
    revenuecat = SlowRevenueCat()

    assert await _grant(factory, revenuecat)

    order = await load_order(factory)
    assert order.access_starts_at == running_until
    assert revenuecat.end_times == [ms(running_until + PASS)]


async def test_granted_order_is_not_granted_again(factory):
    await seed(factory, momo_order(status="paid"))
    revenuecat = SlowRevenueCat()

    assert await _grant(factory, revenuecat)
    assert await _grant(factory, revenuecat)

    assert len(revenuecat.end_times) == 1


async def test_unpaid_order_is_never_granted(factory):
    await seed(factory, momo_order())
    revenuecat = SlowRevenueCat()

    assert not await _grant(factory, revenuecat)

    assert revenuecat.end_times == []
    assert (await load_order(factory)).access_ends_at is None


async def test_quiz_pass_grant_claims_its_lead(factory):
    lead = WebFunnelLead(
        email=BUYER_EMAIL,
        access_key_hash="0" * 64,
        request_id="momo-lead-request",
        snapshot={},
        snapshot_hash="0" * 64,
        status="payment_verified",
    )
    async with factory() as db:
        db.add(lead)
        await db.commit()
    await seed(
        factory,
        momo_order(
            status="paid",
            channel="web_funnel",
            user_id=None,
            buyer_email=BUYER_EMAIL,
            lead_id=lead.id,
        ),
    )

    assert await _grant(factory, SlowRevenueCat())

    async with factory() as db:
        claimed = await db.get(WebFunnelLead, lead.id)
        assert claimed is not None
        assert (claimed.status, claimed.claimed_uid) == ("claimed", FIREBASE_UID)


async def test_window_reserved_for_another_account_moves_to_the_one_granted(factory):
    async with factory() as db:
        db.add(
            User(
                id=OTHER_USER_ID,
                firebase_uid="other-firebase-uid",
                email="other@example.test",
                username="other-buyer",
                password_hash="test",
            )
        )
        await db.commit()
    elsewhere = utc_now() + timedelta(weeks=9)
    await seed(
        factory,
        momo_order(
            status="paid",
            granted_user_id=OTHER_USER_ID,
            access_starts_at=elsewhere - PASS,
            access_ends_at=elsewhere,
        ),
    )
    revenuecat = SlowRevenueCat()

    assert await _grant(factory, revenuecat)

    order = await load_order(factory)
    assert order.granted_user_id == USER_ID
    assert order.access_starts_at - utc_now() < timedelta(minutes=1)
    assert revenuecat.end_times == [ms(order.access_ends_at)]
