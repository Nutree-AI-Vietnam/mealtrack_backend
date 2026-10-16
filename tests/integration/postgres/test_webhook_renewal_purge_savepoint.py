"""A failed renewal trial-push purge must not abort the renewal transaction."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from src.api.routes.v1 import webhook_subscription_lifecycle as lifecycle
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User

pytestmark = pytest.mark.integration

# Fails on the server, so without a SAVEPOINT Postgres aborts the transaction.
_FAILING_PURGE = "SELECT 1 / 0 WHERE CAST(:uid AS text) IS NOT NULL"
_RENEWED_UNTIL_MS = 1_893_456_000_000  # 2030-01-01T00:00:00Z


async def _seed_subscription(session) -> tuple[str, str]:
    user_id = str(uuid4())
    session.add(
        User(
            id=user_id,
            firebase_uid=user_id,
            email=f"{user_id}@example.test",
            username=user_id,
            password_hash="test",
        )
    )
    await session.flush()
    subscription = Subscription(
        user_id=user_id,
        revenuecat_subscriber_id=user_id,
        product_id="premium_monthly",
        platform="ios",
        status="active",
        purchased_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    session.add(subscription)
    await session.commit()
    return user_id, subscription.id


@pytest.mark.asyncio
async def test_failing_statement_without_savepoint_aborts_transaction(pg_session):
    """Control: the injected statement really does poison an open transaction."""
    with pytest.raises(DBAPIError):
        await pg_session.execute(text(_FAILING_PURGE), {"uid": "x"})
    with pytest.raises(DBAPIError, match="current transaction is aborted"):
        await pg_session.execute(text("SELECT 1"))


@pytest.mark.asyncio
async def test_failed_purge_keeps_renewal_committable(
    pg_session, async_session_factory, monkeypatch
):
    user_id, subscription_id = await _seed_subscription(pg_session)
    subscription = await pg_session.get(Subscription, subscription_id)
    monkeypatch.setattr(lifecycle, "text", lambda _sql: text(_FAILING_PURGE))

    with patch.object(
        lifecycle,
        "get_subscription_by_revenuecat_id",
        new=AsyncMock(return_value=subscription),
    ):
        await lifecycle.handle_renewal(
            SimpleNamespace(session=pg_session),
            SimpleNamespace(id=user_id),
            {"app_user_id": user_id, "expiration_at_ms": _RENEWED_UNTIL_MS},
            event_publisher=MagicMock(publish=AsyncMock()),
        )

    # The outer transaction is still usable and the renewal write commits.
    assert (await pg_session.execute(text("SELECT 1"))).scalar_one() == 1
    await pg_session.commit()

    async with async_session_factory() as fresh:
        stored = (
            await fresh.execute(
                select(Subscription).where(Subscription.id == subscription_id)
            )
        ).scalar_one()
    assert stored.status == "active"
    assert stored.expires_at == datetime(2030, 1, 1, tzinfo=UTC)
