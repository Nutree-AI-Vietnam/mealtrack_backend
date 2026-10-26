"""RevenueCat webhook -> subscription access rows on real PostgreSQL.

Runs the webhook route, the real unit of work and repositories, the real
RevenueCat adapter parser and the real access sync and policy. Only the
adapter's HTTP transport is stubbed, with the documented v1 response shape.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.revenuecat_samples import standard_annual_response

from src.api.routes.v1 import webhooks
from src.domain.model.subscription_access import (
    SubscriptionAccessUnavailable,
    evaluate_subscription_access,
)
from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter
from src.infra.database.models.referral.referral_conversion import ReferralConversion
from src.infra.database.models.referral.referral_wallet import ReferralWallet
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.services.subscription_access_refresh_service import (
    RevenueCatAccessRefreshService,
)

pytestmark = pytest.mark.integration

Answer = Callable[[], Awaitable[tuple[int, Any]]]


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
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.delenv("REVENUECAT_WEBHOOK_AUTH_HEADER", raising=False)
    monkeypatch.delenv("AFFILIATE_INTEGRATION_ENABLED", raising=False)
    yield factory


@contextmanager
def _revenuecat(answers: dict[str, Answer]):
    async def get(url, **_kwargs):
        status_code, body = await answers[url.rsplit("/", 1)[-1]]()
        response = MagicMock(status_code=status_code, headers={})
        response.json.return_value = body
        return response

    with (
        patch("httpx.AsyncClient") as client,
        patch.object(
            webhooks,
            "_get_subscription_service",
            return_value=RevenueCatAdapter(api_key="test-key"),
        ),
        patch.object(
            webhooks,
            "_get_event_publisher",
            return_value=MagicMock(publish=AsyncMock()),
        ),
    ):
        client.return_value.__aenter__.return_value.get = AsyncMock(side_effect=get)
        yield


def _ok(body: dict) -> Answer:
    async def answer():
        return 200, body

    return answer


def _annual(request_date: datetime, expires_in: timedelta) -> dict:
    return standard_annual_response(
        request_date=request_date, expires_date=request_date + expires_in
    )


async def _deliver(event: dict) -> dict:
    request = MagicMock()
    request.json = AsyncMock(return_value={"event": event})
    return await webhooks.revenuecat_webhook(request, authorization="webhook-secret")


def _event(event_type: str, app_user_id: str, **values) -> dict:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    return {
        "id": f"evt-{uuid4()}",
        "type": event_type,
        "app_user_id": app_user_id,
        "product_id": "annual",
        "store": "APP_STORE",
        "environment": "PRODUCTION",
        "event_timestamp_ms": now_ms,
        "purchased_at_ms": now_ms,
        "expiration_at_ms": now_ms + 30 * 86_400_000,
        "transaction_id": "1000000123456789",
    } | values


async def _user(factory, name: str) -> User:
    user = User(
        id=str(uuid4()),
        firebase_uid=f"uid-{name}-{uuid4()}",
        email=f"{name}-{uuid4()}@example.com",
        username=f"{name}-{uuid4()}",
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
                    select(Subscription).where(Subscription.user_id == user.id)
                )
            ).all()
        )


@pytest.mark.asyncio
async def test_initial_purchase_writes_one_verified_row(app_sessions):
    user = await _user(app_sessions, "buyer")
    snapshot_at = datetime.now(UTC).replace(microsecond=0)

    with _revenuecat(
        {user.firebase_uid: _ok(_annual(snapshot_at, timedelta(days=365)))}
    ):
        result = await _deliver(_event("INITIAL_PURCHASE", user.firebase_uid))

    assert result == {"status": "success"}
    (row,) = await _rows(app_sessions, user)
    assert (row.provider, row.status, row.product_id) == (
        "revenuecat",
        "active",
        "annual",
    )
    assert row.provider_snapshot_at == snapshot_at
    assert row.provider_sync_required_at is None
    assert row.store_transaction_id == "1000000652379790"
    assert evaluate_subscription_access([row]).has_access is True


@pytest.mark.asyncio
async def test_unreadable_revenuecat_keeps_the_purchase_and_leaves_access_unresolved(
    app_sessions,
):
    user = await _user(app_sessions, "buyer")

    async def down():
        return 503, None

    with _revenuecat({user.firebase_uid: down}):
        result = await _deliver(_event("INITIAL_PURCHASE", user.firebase_uid))

    assert result == {"status": "success"}
    (row,) = await _rows(app_sessions, user)
    assert row.status == "active"
    assert row.provider_sync_required_at is not None
    with pytest.raises(SubscriptionAccessUnavailable):
        evaluate_subscription_access([row])

    # The next readable snapshot repairs it.
    repaired_at = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=5)
    with _revenuecat(
        {user.firebase_uid: _ok(_annual(repaired_at, timedelta(days=365)))}
    ):
        await _deliver(_event("RENEWAL", user.firebase_uid))

    (row,) = await _rows(app_sessions, user)
    assert row.provider_sync_required_at is None
    assert evaluate_subscription_access([row]).has_access is True


@pytest.mark.asyncio
async def test_users_row_writes_during_the_revenuecat_call_do_not_fail_the_sync(
    app_sessions,
):
    """/v1/users/sync writes last_accessed on every call; that is not a conflict."""
    user = await _user(app_sessions, "buyer")
    snapshot_at = datetime.now(UTC).replace(microsecond=0)

    async def answer_after_a_profile_write():
        async with app_sessions() as session:
            await session.execute(
                update(User)
                .where(User.id == user.id)
                .values(last_accessed=datetime.now(UTC))
            )
            await session.commit()
        return 200, _annual(snapshot_at, timedelta(days=365))

    with _revenuecat({user.firebase_uid: answer_after_a_profile_write}):
        result = await _deliver(_event("INITIAL_PURCHASE", user.firebase_uid))

    assert result == {"status": "success"}
    (row,) = await _rows(app_sessions, user)
    assert row.provider_snapshot_at == snapshot_at
    assert row.provider_sync_required_at is None


@pytest.mark.asyncio
async def test_concurrent_deliveries_for_one_user_queue_on_the_row_lock(app_sessions):
    """RevenueCat sends BILLING_ISSUE and CANCELLATION(BILLING_ERROR) together."""
    user = await _user(app_sessions, "buyer")
    base = datetime.now(UTC).replace(microsecond=0)
    with _revenuecat({user.firebase_uid: _ok(_annual(base, timedelta(days=30)))}):
        await _deliver(_event("INITIAL_PURCHASE", user.firebase_uid))

    later = base + timedelta(seconds=10)
    with _revenuecat({user.firebase_uid: _ok(_annual(later, timedelta(days=30)))}):
        results = await asyncio.gather(
            _deliver(_event("BILLING_ISSUE", user.firebase_uid)),
            _deliver(
                _event("CANCELLATION", user.firebase_uid, cancel_reason="BILLING_ERROR")
            ),
        )

    assert results == [{"status": "success"}, {"status": "success"}]
    (row,) = await _rows(app_sessions, user)
    assert row.provider_snapshot_at == later
    assert row.provider_sync_required_at is None


@pytest.mark.asyncio
async def test_an_older_snapshot_never_overwrites_a_newer_one(app_sessions):
    user = await _user(app_sessions, "buyer")
    newer = datetime.now(UTC).replace(microsecond=0)
    older = newer - timedelta(minutes=5)
    with _revenuecat({user.firebase_uid: _ok(_annual(newer, timedelta(days=365)))}):
        await _deliver(_event("INITIAL_PURCHASE", user.firebase_uid))

    with _revenuecat({user.firebase_uid: _ok(_annual(older, timedelta(days=-1)))}):
        await _deliver(_event("RENEWAL", user.firebase_uid))

    (row,) = await _rows(app_sessions, user)
    assert row.provider_snapshot_at == newer
    assert row.expires_at == newer + timedelta(days=365)


@pytest.mark.asyncio
async def test_transfer_moves_only_what_revenuecat_moved_and_never_touches_momo(
    app_sessions,
):
    source = await _user(app_sessions, "source")
    target = await _user(app_sessions, "target")
    now = datetime.now(UTC).replace(microsecond=0)
    async with app_sessions() as session:
        for product_id, provider, platform in (
            ("annual", "revenuecat", "ios"),
            ("momo_pass", "momo", "web"),
        ):
            session.add(
                Subscription(
                    user_id=source.id,
                    revenuecat_subscriber_id=source.firebase_uid,
                    provider=provider,
                    product_id=product_id,
                    platform=platform,
                    status="active",
                    purchased_at=now - timedelta(days=10),
                    expires_at=now + timedelta(days=200),
                    period_type="normal",
                    provider_snapshot_at=now - timedelta(days=1),
                    is_sandbox=False,
                )
            )
        await session.commit()

    emptied = _annual(now, timedelta(days=365))
    emptied["subscriber"].update(entitlements={}, subscriptions={})
    transfer = {
        "id": f"evt-{uuid4()}",
        "type": "TRANSFER",
        "transferred_from": [source.firebase_uid],
        "transferred_to": [target.firebase_uid],
        "event_timestamp_ms": int(now.timestamp() * 1000),
    }
    with _revenuecat(
        {
            source.firebase_uid: _ok(emptied),
            target.firebase_uid: _ok(_annual(now, timedelta(days=200))),
        }
    ):
        result = await _deliver(transfer)

    assert result == {"status": "success"}
    by_provider = {row.provider: row for row in await _rows(app_sessions, source)}
    assert by_provider["revenuecat"].status == "expired"
    assert by_provider["revenuecat"].revenuecat_subscriber_id == source.firebase_uid
    assert by_provider["momo"].status == "active"
    assert by_provider["momo"].provider_snapshot_at == now - timedelta(days=1)
    (moved,) = await _rows(app_sessions, target)
    assert (moved.product_id, moved.status) == ("annual", "active")
    assert evaluate_subscription_access([moved]).has_access is True


@pytest.mark.asyncio
async def test_initial_purchase_after_the_apps_refresh_credits_the_referral(
    app_sessions,
):
    """The app refreshes right after buying, so the row exists before the
    INITIAL_PURCHASE webhook. The purchase is still the first one: the
    referrer is credited and the lifecycle event says so."""
    referrer = await _user(app_sessions, "referrer")
    buyer = await _user(app_sessions, "buyer")
    async with app_sessions() as session:
        session.add(
            ReferralConversion(
                referrer_user_id=referrer.id,
                referred_user_id=buyer.id,
                code_used="ABC123",
                status="pending",
                commission_amount=2.0,
                commission_currency="USD",
                commission_amount_vnd=50000,
            )
        )
        await session.commit()
    read_at = datetime.now(UTC).replace(microsecond=0)
    answers = {buyer.firebase_uid: _ok(_annual(read_at, timedelta(days=365)))}

    with _revenuecat(answers):
        refreshed = await RevenueCatAccessRefreshService(
            lambda: RevenueCatAdapter(api_key="test-key")
        ).refresh(buyer.id)
    assert refreshed.result == "applied"

    publisher = MagicMock(publish=AsyncMock())
    with (
        _revenuecat(answers),
        patch.object(webhooks, "_get_event_publisher", return_value=publisher),
    ):
        result = await _deliver(_event("INITIAL_PURCHASE", buyer.firebase_uid))

    assert result == {"status": "success"}
    async with app_sessions() as session:
        conversion = await session.scalar(
            select(ReferralConversion).where(
                ReferralConversion.referred_user_id == buyer.id
            )
        )
        wallet = await session.get(ReferralWallet, referrer.id)
    assert conversion.status == "converted"
    assert wallet.balance == 50000
    (published,) = publisher.publish.await_args_list
    assert published.args[0]["data"]["lifecycle_type"] == (
        "subscription_initial_purchase"
    )
    (row,) = await _rows(app_sessions, buyer)
    assert row.provider_snapshot_at == read_at
    assert evaluate_subscription_access([row]).has_access is True
