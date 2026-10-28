"""Marker counts behind GET /v1/health/subscription-access, on PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.handlers.query_handlers.get_subscription_access_health_query_handler import (
    GetSubscriptionAccessHealthQueryHandler,
)
from src.app.queries.subscription_access import GetSubscriptionAccessHealthQuery
from src.domain.ports.subscription_access_support_repository_port import (
    AccessMarkerHealth,
)
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.repositories.subscription_access_support_repository import (
    SubscriptionAccessSupportRepository,
    marker_health_statement,
)

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC).replace(microsecond=0)


@pytest_asyncio.fixture
async def app_sessions(
    monkeypatch, pg_session, async_session_factory
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = async_sessionmaker(
        bind=async_session_factory.kw["bind"],
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    monkeypatch.setattr("src.infra.database.uow_async.AsyncSessionLocal", factory)
    yield factory


async def _user_with_rows(factory, *rows: dict, live: bool = True) -> User:
    user = User(
        id=str(uuid4()),
        firebase_uid=f"uid-{uuid4()}",
        email=f"buyer-{uuid4()}@example.com",
        username=f"buyer-{uuid4()}",
        password_hash="unused",
        is_active=live,
        deleted_at=None if live else NOW,
    )
    async with factory() as session:
        session.add(user)
        await session.flush()
        for index, values in enumerate(rows):
            session.add(
                Subscription(
                    **(
                        {
                            "id": str(uuid4()),
                            "user_id": user.id,
                            "provider": "revenuecat",
                            "revenuecat_subscriber_id": user.firebase_uid,
                            "product_id": "annual",
                            "platform": "ios",
                            "status": "cancelled",
                            "purchased_at": NOW - timedelta(days=30 + index),
                            "expires_at": NOW + timedelta(days=300),
                            "provider_snapshot_at": NOW - timedelta(days=2),
                            "is_sandbox": False,
                        }
                        | values
                    )
                )
            )
        await session.commit()
    return user


def _ago(**delta) -> datetime:
    return NOW - timedelta(**delta)


@pytest.mark.asyncio
async def test_counts_markers_of_live_revenuecat_rows(app_sessions):
    # Two marked rows, one user: counted once among users, oldest wins.
    await _user_with_rows(
        app_sessions,
        {"provider_sync_required_at": _ago(minutes=45)},
        {"provider_sync_required_at": _ago(hours=2)},
    )
    # Marked recently: a sync-required user, not a stale one.
    await _user_with_rows(app_sessions, {"provider_sync_required_at": _ago(minutes=5)})
    # A refund whose read failed, 90 minutes ago (marked as sync required too).
    await _user_with_rows(
        app_sessions,
        {
            "provider_sync_required_at": _ago(minutes=90),
            "provider_refund_pending_at": _ago(minutes=90),
        },
    )
    # A fresh refund and a transfer source.
    await _user_with_rows(
        app_sessions,
        {
            "provider_sync_required_at": _ago(minutes=10),
            "provider_refund_pending_at": _ago(minutes=10),
        },
    )
    await _user_with_rows(
        app_sessions,
        {
            "provider_sync_required_at": _ago(minutes=20),
            "provider_transfer_source_at": _ago(minutes=20),
        },
    )
    # Never counted: unmarked rows, deleted accounts, other providers.
    await _user_with_rows(app_sessions, {})
    await _user_with_rows(
        app_sessions, {"provider_sync_required_at": _ago(days=30)}, live=False
    )
    await _user_with_rows(
        app_sessions,
        {
            "provider": "momo",
            "revenuecat_subscriber_id": None,
            "provider_sync_required_at": _ago(days=20),
        },
    )

    counts = await SubscriptionAccessSupportRepository().marker_health(
        sync_required_before=_ago(minutes=30), refund_pending_before=_ago(minutes=60)
    )

    assert counts == AccessMarkerHealth(
        sync_required_users=5,
        stale_sync_required_users=2,
        refund_pending_rows=2,
        stale_refund_pending_rows=1,
        transfer_source_rows=1,
        oldest_marker_at=_ago(hours=2),
    )


@pytest.mark.asyncio
async def test_nothing_marked_counts_zero(app_sessions):
    await _user_with_rows(app_sessions, {})

    counts = await SubscriptionAccessSupportRepository().marker_health(
        sync_required_before=NOW, refund_pending_before=NOW
    )

    assert counts == AccessMarkerHealth(0, 0, 0, 0, 0, None)


@pytest.mark.asyncio
async def test_the_report_degrades_on_a_stale_refund(app_sessions, monkeypatch):
    for name in (
        "SUBSCRIPTION_ACCESS_ALERT_SYNC_REQUIRED_MINUTES",
        "SUBSCRIPTION_ACCESS_ALERT_SYNC_REQUIRED_USERS",
        "SUBSCRIPTION_ACCESS_ALERT_REFUND_PENDING_MINUTES",
        "SUBSCRIPTION_ACCESS_ALERT_OLDEST_MARKER_HOURS",
    ):
        monkeypatch.delenv(name, raising=False)
    await _user_with_rows(
        app_sessions,
        {
            "provider_sync_required_at": _ago(minutes=75),
            "provider_refund_pending_at": _ago(minutes=75),
        },
    )

    report = await GetSubscriptionAccessHealthQueryHandler(
        SubscriptionAccessSupportRepository()
    ).handle(GetSubscriptionAccessHealthQuery())

    assert report.status == "degraded"
    assert [reason.code for reason in report.reasons] == ["refund_pending_stale"]
    assert report.counts.stale_sync_required_users == 1


@pytest.mark.asyncio
async def test_the_monitoring_query_can_use_the_partial_index(app_sessions):
    statement = marker_health_statement(
        sync_required_before=NOW, refund_pending_before=NOW
    )
    compiled = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )

    async with app_sessions() as session:
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            row[0] for row in (await session.execute(text(f"EXPLAIN {compiled}"))).all()
        )

    assert "ix_subscriptions_access_markers" in plan
