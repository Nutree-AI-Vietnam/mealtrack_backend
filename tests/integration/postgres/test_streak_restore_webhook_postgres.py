"""RevenueCat pack events through POST /v1/webhooks/revenuecat on real PostgreSQL."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select

from src.api.routes.v1.webhooks import router as webhooks_router
from src.app.services.streak_restore_ledger import (
    PackPurchase,
    credit_pack,
    spend_restores,
)
from src.domain.model.streak import StreakRestorePolicy
from src.infra.database.models.streak_restore import StreakRestoreLedgerORM
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork

pytestmark = pytest.mark.integration

SECRET = "webhook-test-secret"
DAY_1, DAY_2 = date(2026, 10, 26), date(2026, 10, 27)


@pytest_asyncio.fixture
async def factory(monkeypatch, pg_session, async_session_factory):
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("REVENUECAT_WEBHOOK_AUTH_HEADER", raising=False)
    monkeypatch.delenv("AFFILIATE_INTEGRATION_ENABLED", raising=False)
    routes = "src.api.routes.v1.webhooks"
    monkeypatch.setattr(f"{routes}._get_event_publisher", MagicMock)
    monkeypatch.setattr(f"{routes}.get_streak_restore_policy", StreakRestorePolicy)
    monkeypatch.setattr(f"{routes}.get_cache_service", lambda: None)
    return async_session_factory


async def _user(session) -> User:
    user = User(
        id=str(uuid4()),
        firebase_uid=f"uid-{uuid4()}",
        email=f"{uuid4()}@example.com",
        username=f"user-{uuid4()}",
        password_hash="unused",
    )
    session.add(user)
    await session.commit()
    return user


async def _claim(user_id: str, count: int = 3) -> None:
    """Credit the pack the way the restore endpoint's claim does."""
    purchase = PackPurchase(
        f"nutree_streak_restore_{count}", "tx-1", count, "app_store", "production"
    )
    async with AsyncUnitOfWork() as uow:
        await uow.streak_restores.lock_user(user_id)
        await credit_pack(uow, user_id, purchase)


async def _webhook(user: User, event_type: str, count: int = 3) -> httpx.Response:
    app = FastAPI()
    app.include_router(webhooks_router)
    event = {
        "id": str(uuid4()),
        "type": event_type,
        "app_user_id": user.firebase_uid,
        "product_id": f"nutree_streak_restore_{count}",
        "transaction_id": "tx-1",
        "store": "APP_STORE",
        "environment": "PRODUCTION",
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/v1/webhooks/revenuecat",
            json={"event": event},
            headers={"Authorization": SECRET},
        )


async def _rows(factory, user_id: str) -> list[StreakRestoreLedgerORM]:
    async with factory() as session:
        result = await session.execute(
            select(StreakRestoreLedgerORM).where(
                StreakRestoreLedgerORM.user_id == user_id
            )
        )
        return list(result.scalars())


async def _balance(user_id: str) -> int:
    async with AsyncUnitOfWork() as uow:
        return await uow.streak_restores.balance(user_id)


async def _subscriptions(factory, user_id: str) -> list[Subscription]:
    async with factory() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        )
        return list(result.scalars())


@pytest.mark.asyncio
async def test_a_claimed_purchase_redelivered_by_the_webhook_stays_one_row(
    factory, pg_session
):
    user = await _user(pg_session)
    await _claim(user.id)

    response = await _webhook(user, "NON_RENEWING_PURCHASE")

    assert response.status_code == 200
    assert [row.kind for row in await _rows(factory, user.id)] == ["purchase"]
    assert await _balance(user.id) == 3


@pytest.mark.asyncio
async def test_a_refund_after_a_spend_leaves_a_zero_balance_and_no_subscription(
    factory, pg_session
):
    user = await _user(pg_session)
    assert (await _webhook(user, "NON_RENEWING_PURCHASE")).status_code == 200
    assert await _balance(user.id) == 3
    async with AsyncUnitOfWork() as uow:
        await spend_restores(uow, user.id, [DAY_1, DAY_2])  # free + paid
    assert await _balance(user.id) == 2

    response = await _webhook(user, "CANCELLATION")

    assert response.status_code == 200
    refunds = [row for row in await _rows(factory, user.id) if row.kind == "refund"]
    assert [row.delta for row in refunds] == [-2]
    assert await _balance(user.id) == 0
    assert await _subscriptions(factory, user.id) == []


@pytest.mark.asyncio
async def test_a_refund_before_the_credit_writes_both_rows_netting_zero(
    factory, pg_session
):
    user = await _user(pg_session)
    assert (await _webhook(user, "CANCELLATION")).status_code == 200
    assert (await _webhook(user, "NON_RENEWING_PURCHASE")).status_code == 200
    await _claim(user.id)

    kinds = sorted(row.kind for row in await _rows(factory, user.id))
    assert kinds == ["purchase", "refund"]
    assert await _balance(user.id) == 0


@pytest.mark.asyncio
async def test_a_refund_naming_another_user_debits_the_pack_owner(factory, pg_session):
    owner = await _user(pg_session)
    other = await _user(pg_session)
    await _claim(owner.id)

    response = await _webhook(other, "CANCELLATION")

    assert response.status_code == 200
    assert await _balance(owner.id) == 0
    assert sorted(row.kind for row in await _rows(factory, owner.id)) == [
        "purchase",
        "refund",
    ]
    assert await _rows(factory, other.id) == []
