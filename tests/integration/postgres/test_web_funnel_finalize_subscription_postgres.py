"""Web-funnel finalize applies its RevenueCat snapshot on real PostgreSQL.

Runs finalize_redemption with a real session: the user lock, the access sync
and the policy. The RevenueCat customer is the documented v1 shape.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.revenuecat_samples import standard_annual_response

from src.domain.model.subscription_access import evaluate_subscription_access
from src.domain.ports.subscription_service_port import RevenueCatSnapshot
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.database.models.web_funnel_claim import (
    WebFunnelLead,
    WebFunnelRedemption,
)
from src.infra.services.web_funnel_redemption_completion import finalize_redemption

pytestmark = pytest.mark.integration

LEAD_SNAPSHOT = {
    "gender": "female",
    "height": 168,
    "weight": 62,
    "birth_year": 1995,
    "birth_month": 4,
    "birth_day": 20,
    "job_type": "desk",
    "training_days_per_week": 3,
    "training_minutes_per_session": 45,
    "goal": "recomp",
    "training_level": None,
    "custom_protein_g": None,
    "custom_carbs_g": None,
    "custom_fat_g": None,
}


@pytest_asyncio.fixture
async def sessions(
    pg_session, async_session_factory
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions configured like the app's: no autoflush, no expiry on commit.

    The users truncation does not reach web-funnel rows: remove them after.
    """
    factory = async_sessionmaker(
        bind=async_session_factory.kw["bind"],
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    yield factory
    async with factory() as session:
        await session.execute(
            delete(WebFunnelRedemption).where(
                WebFunnelRedemption.original_app_user_id.like("$RCAnonymousID:test-%")
            )
        )
        await session.execute(
            delete(WebFunnelLead).where(WebFunnelLead.request_id.like("test-%"))
        )
        await session.commit()


def _web_customer(read_at: datetime, uid: str) -> RevenueCatSnapshot:
    body = standard_annual_response(
        request_date=read_at,
        expires_date=read_at + timedelta(days=30),
        store="stripe",
        store_transaction_id="sub_stripe_1",
    )
    subscriber = body["subscriber"]
    subscriber["subscriptions"] = {
        "web_monthly": subscriber["subscriptions"].pop("annual") | {"is_sandbox": True}
    }
    subscriber["entitlements"]["standard"]["product_identifier"] = "web_monthly"
    return RevenueCatSnapshot(subscriber, read_at, app_user_id=uid)


async def _paid_lead(factory, *, email: str, uid: str) -> tuple[str, str]:
    """A paid lead bound to ``uid``; returns (link hash, web app user id)."""
    link_hash = hashlib.sha256(f"link-{uuid4()}".encode()).hexdigest()
    web_app_user_id = f"$RCAnonymousID:test-{uuid4()}"
    async with factory() as session:
        lead = WebFunnelLead(
            id=str(uuid4()),
            email=email,
            access_key_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
            request_id=f"test-{uuid4()}",
            snapshot=LEAD_SNAPSHOT,
            snapshot_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
            status="payment_verified",
        )
        session.add(lead)
        await session.flush()
        session.add(
            WebFunnelRedemption(
                id=str(uuid4()),
                lead_id=lead.id,
                environment="SANDBOX",
                project="default",
                original_app_user_id=web_app_user_id,
                verified_app_user_id=web_app_user_id,
                entitlement_id="standard",
                product_id="web_monthly",
                verified_at=datetime.now(UTC),
                redemption_link_hash=link_hash,
                preflight_uid=uid,
            )
        )
        await session.commit()
    return link_hash, web_app_user_id


async def _finalize(factory, *, uid, email, lead, snapshot):
    link_hash, web_app_user_id = lead
    async with factory() as session:
        return await finalize_redemption(
            session,
            uid=uid,
            email=email,
            original_app_user_id=web_app_user_id,
            redemption_link_hash=link_hash,
            idempotency_key=f"key-{uuid4()}",
            environment="SANDBOX",
            auth_provider="google.com",
            provider_snapshot=snapshot,
        )


async def _rows(factory, uid: str) -> tuple[User, list[Subscription]]:
    async with factory() as session:
        user = await session.scalar(select(User).where(User.firebase_uid == uid))
        rows = list(
            (
                await session.scalars(
                    select(Subscription).where(Subscription.user_id == user.id)
                )
            ).all()
        )
    return user, rows


@pytest.mark.asyncio
async def test_finalize_creates_the_buyer_and_a_verified_row(sessions):
    uid = f"uid-{uuid4()}"
    email = f"buyer-{uuid4()}@example.com"
    lead = await _paid_lead(sessions, email=email, uid=uid)
    read_at = datetime.now(UTC).replace(microsecond=0)

    result = await _finalize(
        sessions,
        uid=uid,
        email=email,
        lead=lead,
        snapshot=_web_customer(read_at, uid),
    )

    assert result["access_status"] == "active"
    user, (row,) = await _rows(sessions, uid)
    assert user.onboarding_completed is True
    assert (row.product_id, row.provider, row.revenuecat_subscriber_id) == (
        "web_monthly",
        "revenuecat",
        uid,
    )
    assert (row.provider_snapshot_at, row.provider_sync_required_at) == (read_at, None)
    assert evaluate_subscription_access([row]).has_access is True


@pytest.mark.asyncio
async def test_finalize_answers_the_markers_on_the_buyers_other_rows(sessions):
    """A webhook naming the uid failed its read before finalize and left a
    marked placeholder; finalize's snapshot clears it like any sync."""
    uid = f"uid-{uuid4()}"
    email = f"buyer-{uuid4()}@example.com"
    async with sessions() as session:
        user = User(
            id=str(uuid4()),
            firebase_uid=uid,
            email=email,
            username=f"buyer-{uuid4()}",
            password_hash="unused",
        )
        session.add(user)
        await session.flush()
        event_time = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=2)
        session.add(
            Subscription(
                id=str(uuid4()),
                user_id=user.id,
                revenuecat_subscriber_id=uid,
                provider="revenuecat",
                product_id="unknown",
                platform="ios",
                status="expired",
                purchased_at=event_time,
                provider_sync_required_at=event_time,
                is_sandbox=False,
            )
        )
        await session.commit()
    lead = await _paid_lead(sessions, email=email, uid=uid)
    read_at = datetime.now(UTC).replace(microsecond=0)

    await _finalize(
        sessions,
        uid=uid,
        email=email,
        lead=lead,
        snapshot=_web_customer(read_at, uid),
    )

    _, rows = await _rows(sessions, uid)
    by_product = {row.product_id: row for row in rows}
    assert set(by_product) == {"unknown", "web_monthly"}
    assert all(row.provider_sync_required_at is None for row in rows)
    assert all(row.provider_snapshot_at == read_at for row in rows)
    assert by_product["unknown"].status == "expired"
    assert evaluate_subscription_access(rows).has_access is True
