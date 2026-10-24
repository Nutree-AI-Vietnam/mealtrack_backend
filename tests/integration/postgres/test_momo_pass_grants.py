"""Crediting and syncing MoMo passes on Postgres: stacking, retries and leads."""

from __future__ import annotations

import logging
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
    URLS,
    USER_ID,
    FakeMomo,
    SlowRevenueCat,
    deliver,
    load_order,
    momo_order,
    momo_sessions,
    move_windows_back,
    ms,
    seed,
)

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.web_funnel_claim import WebFunnelLead
from src.infra.services.momo_pass_entitlement import MomoPassEntitlements
from src.infra.services.momo_pass_service import MomoPassService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def factory(migrated_database: str) -> AsyncIterator[async_sessionmaker]:
    async with momo_sessions(migrated_database) as sessions:
        yield sessions


async def _has_active_pass(sessions: async_sessionmaker) -> bool:
    async with sessions() as db:
        return await MomoPassEntitlements(None).has_active_pass(db, USER_ID)


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

    assert await deliver(factory, revenuecat)

    order = await load_order(factory)
    assert order.access_starts_at == running_until
    assert revenuecat.end_times == [ms(running_until + PASS)]


async def test_granted_order_is_not_sent_again(factory):
    await seed(factory, momo_order(status="paid"))
    revenuecat = SlowRevenueCat()

    assert await deliver(factory, revenuecat)
    assert await deliver(factory, revenuecat)

    assert len(revenuecat.end_times) == 1


async def test_unpaid_order_is_never_credited(factory):
    await seed(factory, momo_order())
    revenuecat = SlowRevenueCat()

    await deliver(factory, revenuecat)

    assert revenuecat.end_times == []
    assert (await load_order(factory)).access_ends_at is None


async def test_lost_revenuecat_answer_is_retried_with_the_same_end(factory):
    await seed(factory, momo_order(status="paid"))
    lost = SlowRevenueCat(ok=False)
    assert not await deliver(factory, lost)
    assert not await _has_active_pass(factory)
    retry = SlowRevenueCat()
    service = MomoPassService(factory, FakeMomo(), MomoPassEntitlements(retry), URLS)

    # The buyer's next status poll retries the sync.
    polled = await service.app_order(ORDER_ID, USER_ID)

    order = await load_order(factory)
    assert polled is not None and polled["status"] == "paid"
    assert retry.end_times == lost.end_times == [ms(order.access_ends_at)]
    assert order.granted_at is not None
    assert await _has_active_pass(factory)


async def test_credit_whose_window_ended_is_not_sent_and_is_marked(factory, caplog):
    await seed(factory, momo_order(status="paid"))
    assert not await deliver(factory, SlowRevenueCat(ok=False))
    # RevenueCat stayed down, or nobody retried, for longer than the pass.
    await move_windows_back(factory, PASS + timedelta(days=1))
    revenuecat = SlowRevenueCat()

    with caplog.at_level(logging.ERROR):
        assert await deliver(factory, revenuecat)

    assert revenuecat.end_times == []
    assert (await load_order(factory)).granted_at is not None
    assert "lapsed before they reached RevenueCat" in caplog.text
    assert not await _has_active_pass(factory)


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

    assert await deliver(factory, SlowRevenueCat())

    async with factory() as db:
        claimed = await db.get(WebFunnelLead, lead.id)
        assert claimed is not None
        assert (claimed.status, claimed.claimed_uid) == ("claimed", FIREBASE_UID)
