"""Intro or regular MoMo price on Postgres, without leaking who bought before.

A quiz visitor types any email, so a quiz price depends only on that lead's
own orders. A signed-in app buyer is priced on their account and their proven
email.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.integration.postgres.momo_pass_fixtures import (
    BUYER_EMAIL,
    URLS,
    USER_ID,
    FakeMomo,
    momo_order,
    momo_sessions,
    seed,
)

from src.infra.database.models.web_funnel_claim import WebFunnelLead
from src.infra.services.momo_pass_entitlement import MomoPassEntitlements
from src.infra.services.momo_pass_service import MomoBuyer, MomoPassService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def factory(migrated_database: str) -> AsyncIterator[async_sessionmaker]:
    async with momo_sessions(migrated_database) as sessions:
        yield sessions


async def _lead(sessions: async_sessionmaker, request_id: str) -> str:
    lead = WebFunnelLead(
        email=BUYER_EMAIL,
        access_key_hash="0" * 64,
        request_id=request_id,
        snapshot={},
        snapshot_hash="0" * 64,
        status="draft",
    )
    async with sessions() as db:
        db.add(lead)
        await db.commit()
    return lead.id


async def _intro(sessions: async_sessionmaker, buyer: MomoBuyer) -> bool:
    service = MomoPassService(sessions, FakeMomo(), MomoPassEntitlements(None), URLS)
    return all(plan["intro"] for plan in await service.plans_for(buyer))


async def test_quiz_price_ignores_other_purchases_under_the_same_email(factory):
    earlier_lead = await _lead(factory, "earlier-quiz")
    await seed(
        factory,
        momo_order(status="paid", buyer_email=BUYER_EMAIL),
        momo_order(
            "c" * 32,
            status="paid",
            channel="web_funnel",
            user_id=None,
            buyer_email=BUYER_EMAIL,
            lead_id=earlier_lead,
        ),
    )
    new_lead = await _lead(factory, "new-quiz")

    new_visitor = MomoBuyer(channel="web_funnel", lead_id=new_lead, email=BUYER_EMAIL)
    returning = MomoBuyer(channel="web_funnel", lead_id=earlier_lead, email="x@y.z")

    assert await _intro(factory, new_visitor)
    assert not await _intro(factory, returning)


async def test_app_price_counts_the_account_and_its_proven_email(factory):
    await seed(
        factory,
        momo_order(
            status="paid", channel="web_funnel", user_id=None, buyer_email=BUYER_EMAIL
        ),
    )

    assert await _intro(factory, MomoBuyer(channel="app", user_id=USER_ID))
    assert not await _intro(
        factory, MomoBuyer(channel="app", user_id=USER_ID, email=BUYER_EMAIL)
    )
