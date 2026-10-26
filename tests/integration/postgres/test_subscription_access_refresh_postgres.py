"""Entitlements refresh and provider-safe rows on real PostgreSQL.

Runs the real unit of work, repositories, RevenueCat adapter parser, access
sync and policy. Only the adapter's HTTP transport is stubbed, with the
documented v1 response shape.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.revenuecat_samples import (
    empty_customer_response,
    standard_annual_response,
)

from src.app.handlers.query_handlers.get_my_entitlements_query_handler import (
    GetMyEntitlementsQueryHandler,
)
from src.app.queries.subscription_access import GetMyEntitlementsQuery
from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.services.subscription_access_refresh_service import (
    RevenueCatAccessRefreshService,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def app_sessions(
    monkeypatch, pg_session, async_session_factory
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions configured like the app's: no autoflush, no expiry on commit."""
    factory = async_sessionmaker(
        bind=async_session_factory.kw["bind"],
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    monkeypatch.setattr("src.infra.database.uow_async.AsyncSessionLocal", factory)
    yield factory


@contextmanager
def _revenuecat(status_code: int, body: Any):
    async def get(url, **_kwargs):
        response = MagicMock(status_code=status_code, headers={})
        response.json.return_value = body
        return response

    with patch("httpx.AsyncClient") as client:
        client.return_value.__aenter__.return_value.get = AsyncMock(side_effect=get)
        yield


async def _user(factory) -> User:
    user = User(
        id=str(uuid4()),
        firebase_uid=f"uid-{uuid4()}",
        email=f"buyer-{uuid4()}@example.com",
        username=f"buyer-{uuid4()}",
        password_hash="unused",
    )
    async with factory() as session:
        session.add(user)
        await session.commit()
    return user


async def _rows(factory, user: User) -> list[Subscription]:
    async with factory() as session:
        return list(
            (
                await session.scalars(
                    select(Subscription)
                    .where(Subscription.user_id == user.id)
                    .order_by(Subscription.provider)
                )
            ).all()
        )


async def _momo_row(factory, user: User, *, expires_in: timedelta) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    async with factory() as session:
        session.add(
            Subscription(
                id=str(uuid4()),
                user_id=user.id,
                provider="momo",
                revenuecat_subscriber_id=None,
                product_id="momo_pass_90d",
                platform="web",
                status="active",
                purchased_at=now,
                expires_at=now + expires_in,
                provider_snapshot_at=now,
                is_sandbox=False,
            )
        )
        await session.commit()


def _refresher() -> RevenueCatAccessRefreshService:
    return RevenueCatAccessRefreshService(lambda: RevenueCatAdapter(api_key="test-key"))


async def _entitlements(user: User) -> dict:
    return await GetMyEntitlementsQueryHandler(uow_factory=AsyncUnitOfWork).handle(
        GetMyEntitlementsQuery(user.id)
    )


@pytest.mark.asyncio
async def test_refresh_writes_and_commits_the_verified_row(app_sessions):
    user = await _user(app_sessions)
    snapshot_at = datetime.now(UTC).replace(microsecond=0)
    body = standard_annual_response(
        request_date=snapshot_at, expires_date=snapshot_at + timedelta(days=365)
    )

    with _revenuecat(200, body):
        result = await _refresher().refresh(user.id)

    assert result.result == "applied"
    (row,) = await _rows(app_sessions, user)
    assert (row.provider, row.status, row.revenuecat_subscriber_id) == (
        "revenuecat",
        "active",
        user.firebase_uid,
    )
    assert row.provider_snapshot_at == snapshot_at
    entitlements = await _entitlements(user)
    assert entitlements["has_access"] is True
    assert entitlements["provider"] == "revenuecat"
    assert entitlements["product_id"] == row.product_id
    assert entitlements["will_renew"] is True


@pytest.mark.asyncio
async def test_unreadable_revenuecat_changes_nothing(app_sessions):
    user = await _user(app_sessions)

    from src.domain.ports.subscription_service_port import (
        RevenueCatSnapshotUnavailable,
    )

    with _revenuecat(503, None), pytest.raises(RevenueCatSnapshotUnavailable):
        await _refresher().refresh(user.id)

    assert await _rows(app_sessions, user) == []


@pytest.mark.asyncio
async def test_momo_row_without_revenuecat_id_survives_a_revenuecat_refresh(
    app_sessions,
):
    """A row another provider owns has no RevenueCat id, loads fine, and a
    RevenueCat snapshot (here: no purchases at all) never touches it."""
    user = await _user(app_sessions)
    await _momo_row(app_sessions, user, expires_in=timedelta(days=80))

    with _revenuecat(200, empty_customer_response(app_user_id=user.firebase_uid)):
        result = await _refresher().refresh(user.id)

    assert result.result == "absent"
    (row,) = await _rows(app_sessions, user)
    assert (row.provider, row.status, row.revenuecat_subscriber_id) == (
        "momo",
        "active",
        None,
    )
    entitlements = await _entitlements(user)
    assert (entitlements["has_access"], entitlements["provider"]) == (True, "momo")
    assert entitlements["will_renew"] is False


@pytest.mark.asyncio
async def test_revenuecat_grant_and_momo_pass_coexist(app_sessions):
    user = await _user(app_sessions)
    await _momo_row(app_sessions, user, expires_in=timedelta(days=500))
    snapshot_at = datetime.now(UTC).replace(microsecond=0)
    body = standard_annual_response(
        request_date=snapshot_at, expires_date=snapshot_at + timedelta(days=365)
    )

    with _revenuecat(200, body):
        await _refresher().refresh(user.id)

    momo, revenuecat = await _rows(app_sessions, user)
    assert (momo.provider, momo.status) == ("momo", "active")
    assert (revenuecat.provider, revenuecat.status) == ("revenuecat", "active")
    entitlements = await _entitlements(user)
    # The longest grant describes the access.
    assert (entitlements["provider"], entitlements["product_id"]) == (
        "momo",
        "momo_pass_90d",
    )


@pytest.mark.asyncio
async def test_insert_without_provider_is_refused(app_sessions):
    user = await _user(app_sessions)
    async with app_sessions() as session:
        session.add(
            Subscription(
                id=str(uuid4()),
                user_id=user.id,
                product_id="momo_pass_90d",
                platform="web",
                status="active",
                purchased_at=datetime.now(UTC),
                is_sandbox=False,
            )
        )
        with pytest.raises(ValueError, match="provider must be set explicitly"):
            await session.flush()
