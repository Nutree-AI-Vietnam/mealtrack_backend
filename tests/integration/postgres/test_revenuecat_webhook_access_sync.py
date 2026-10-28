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
from src.app.handlers.query_handlers.check_subscription_access_query_handler import (
    CheckSubscriptionAccessQueryHandler,
)
from src.app.queries.subscription_access import CheckSubscriptionAccessQuery
from src.domain.model.subscription_access import (
    SubscriptionAccessUnavailable,
    evaluate_subscription_access,
)
from src.domain.ports.subscription_service_port import RevenueCatSnapshot
from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter
from src.infra.database.models.referral.referral_conversion import ReferralConversion
from src.infra.database.models.referral.referral_wallet import ReferralWallet
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.services.subscription_access_refresh_service import (
    RevenueCatAccessRefreshService,
)
from src.infra.services.subscription_access_sync_service import (
    sync_revenuecat_access,
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
async def test_sync_sees_what_another_writer_committed_before_the_lock(app_sessions):
    """The webhook's user lookup can load a subscription row before the lock.
    The locked re-select must replace its stale values; otherwise an older
    read passes the stale check and overwrites newer committed facts."""
    user = await _user(app_sessions, "buyer")
    older = datetime.now(UTC).replace(microsecond=0)
    newer = older + timedelta(minutes=5)
    async with app_sessions() as session:
        session.add(
            Subscription(
                user_id=user.id,
                revenuecat_subscriber_id=user.firebase_uid,
                provider="revenuecat",
                product_id="annual",
                platform="ios",
                status="active",
                purchased_at=older - timedelta(days=10),
                expires_at=older + timedelta(days=30),
                period_type="normal",
                provider_snapshot_at=older - timedelta(days=1),
                is_sandbox=False,
            )
        )
        await session.commit()

    async with app_sessions() as session:
        # Loaded unlocked, as find_user_for_revenuecat_event's fallback does.
        (loaded,) = (
            await session.scalars(
                select(Subscription).where(Subscription.user_id == user.id)
            )
        ).all()
        async with app_sessions() as writer:  # a delivery that committed first
            await writer.execute(
                update(Subscription)
                .where(Subscription.id == loaded.id)
                .values(
                    provider_snapshot_at=newer, expires_at=newer + timedelta(days=365)
                )
            )
            await writer.commit()
        lapsed = _annual(older, timedelta(days=-1))
        outcome = await sync_revenuecat_access(
            session,
            await session.get(User, user.id),
            snapshot=RevenueCatSnapshot(
                lapsed["subscriber"], older, app_user_id=user.firebase_uid
            ),
        )
        await session.commit()

    assert outcome.result == "stale_skipped"
    (row,) = await _rows(app_sessions, user)
    assert (row.status, row.provider_snapshot_at) == ("active", newer)
    assert row.expires_at == newer + timedelta(days=365)


@pytest.mark.asyncio
async def test_an_old_products_expiration_during_an_outage_keeps_the_upgrade(
    app_sessions,
):
    """Upgrade monthly -> annual, then the monthly EXPIRATION arrives while
    RevenueCat is down. It lands on the monthly row, so the paying user keeps
    access through the legacy fallback until a snapshot repairs the markers."""
    user = await _user(app_sessions, "upgrader")
    now = datetime.now(UTC).replace(microsecond=0)
    async with app_sessions() as session:
        for product_id, status, purchased_days_ago, expires_in_days in (
            ("onemonth", "expired", 400, -2),
            ("annual", "active", 2, 363),
        ):
            session.add(
                Subscription(
                    user_id=user.id,
                    revenuecat_subscriber_id=user.firebase_uid,
                    provider="revenuecat",
                    product_id=product_id,
                    platform="ios",
                    status=status,
                    purchased_at=now - timedelta(days=purchased_days_ago),
                    expires_at=now + timedelta(days=expires_in_days),
                    period_type="normal",
                    provider_snapshot_at=now - timedelta(days=1),
                    is_sandbox=False,
                )
            )
        await session.commit()

    async def down():
        return 503, None

    with _revenuecat({user.firebase_uid: down}):
        result = await _deliver(
            _event("EXPIRATION", user.firebase_uid, product_id="onemonth")
        )

    assert result == {"status": "success"}
    rows = {row.product_id: row for row in await _rows(app_sessions, user)}
    assert rows["annual"].status == "active"
    assert rows["annual"].expires_at == now + timedelta(days=363)
    assert rows["onemonth"].status == "expired"
    assert all(row.provider_sync_required_at for row in rows.values())
    with pytest.raises(SubscriptionAccessUnavailable):
        evaluate_subscription_access(list(rows.values()))
    enforcement = CheckSubscriptionAccessQueryHandler(uow_factory=AsyncUnitOfWork)
    assert await enforcement.handle(CheckSubscriptionAccessQuery(user.id)) is True


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


@pytest.mark.asyncio
async def test_refund_ends_access_from_the_snapshot_and_moves_no_commission(
    app_sessions,
):
    """A refund CANCELLATION runs production's cancellation path: the referrer
    keeps the commission and the lifecycle event is 'subscription_canceled'.
    The snapshot, which no longer grants, revokes the row as 'refunded'."""
    referrer = await _user(app_sessions, "referrer")
    buyer = await _user(app_sessions, "buyer")
    now = datetime.now(UTC).replace(microsecond=0)
    async with app_sessions() as session:
        session.add(
            ReferralConversion(
                referrer_user_id=referrer.id,
                referred_user_id=buyer.id,
                code_used="ABC123",
                status="converted",
                commission_amount=2.0,
                commission_currency="USD",
                commission_amount_vnd=50000,
                converted_at=now - timedelta(days=30),
            )
        )
        session.add(
            ReferralWallet(
                user_id=referrer.id,
                balance=50000,
                total_earned=50000,
                updated_at=now - timedelta(days=30),
            )
        )
        session.add(
            Subscription(
                user_id=buyer.id,
                revenuecat_subscriber_id=buyer.firebase_uid,
                provider="revenuecat",
                product_id="annual",
                platform="ios",
                status="active",
                purchased_at=now - timedelta(days=30),
                expires_at=now + timedelta(days=335),
                period_type="normal",
                provider_snapshot_at=now - timedelta(days=1),
                is_sandbox=False,
            )
        )
        await session.commit()
    refunded = _annual(now, timedelta(minutes=-1))
    publisher = MagicMock(publish=AsyncMock())

    with (
        _revenuecat({buyer.firebase_uid: _ok(refunded)}),
        patch.object(webhooks, "_get_event_publisher", return_value=publisher),
    ):
        result = await _deliver(
            _event(
                "CANCELLATION",
                buyer.firebase_uid,
                cancel_reason="CUSTOMER_SUPPORT",
                price=-59.99,
            )
        )

    assert result == {"status": "success"}
    (row,) = await _rows(app_sessions, buyer)
    assert (row.status, row.provider_sync_required_at) == ("refunded", None)
    assert evaluate_subscription_access([row]).has_access is False
    async with app_sessions() as session:
        conversion = await session.scalar(
            select(ReferralConversion).where(
                ReferralConversion.referred_user_id == buyer.id
            )
        )
        wallet = await session.get(ReferralWallet, referrer.id)
    assert conversion.status == "converted"
    assert (wallet.balance, wallet.total_revoked) == (50000, 0)
    (published,) = publisher.publish.await_args_list
    assert published.args[0]["data"]["lifecycle_type"] == "subscription_canceled"


@pytest.mark.asyncio
async def test_refresh_resolves_a_transfer_source_whose_read_failed(app_sessions):
    """TRANSFER A -> B while A's RevenueCat read fails: A's row is only marked
    and remembered as a transfer source. A's refresh then reads the emptied
    customer and applies it."""
    source = await _user(app_sessions, "source")
    target = await _user(app_sessions, "target")
    now = datetime.now(UTC).replace(microsecond=0)
    async with app_sessions() as session:
        session.add(
            Subscription(
                user_id=source.id,
                revenuecat_subscriber_id=source.firebase_uid,
                provider="revenuecat",
                product_id="annual",
                platform="ios",
                status="active",
                purchased_at=now - timedelta(days=10),
                expires_at=now + timedelta(days=65),
                period_type="normal",
                provider_snapshot_at=now - timedelta(days=1),
                is_sandbox=False,
            )
        )
        await session.commit()

    async def down():
        return 503, None

    transfer = {
        "id": f"evt-{uuid4()}",
        "type": "TRANSFER",
        "transferred_from": [source.firebase_uid],
        "transferred_to": [target.firebase_uid],
        "event_timestamp_ms": int(now.timestamp() * 1000),
    }
    with _revenuecat(
        {
            source.firebase_uid: down,
            target.firebase_uid: _ok(_annual(now, timedelta(days=65))),
        }
    ):
        assert await _deliver(transfer) == {"status": "success"}

    (row,) = await _rows(app_sessions, source)
    assert (row.status, row.provider_transfer_source_at) == ("active", now)
    assert row.provider_sync_required_at == now

    emptied = _annual(now + timedelta(seconds=5), timedelta(days=365))
    emptied["subscriber"].update(entitlements={}, subscriptions={})
    with _revenuecat({source.firebase_uid: _ok(emptied)}):
        refreshed = await RevenueCatAccessRefreshService(
            lambda: RevenueCatAdapter(api_key="test-key")
        ).refresh(source.id)

    assert refreshed.result == "applied"
    (row,) = await _rows(app_sessions, source)
    assert row.status == "expired"
    assert (row.provider_sync_required_at, row.provider_transfer_source_at) == (
        None,
        None,
    )
    assert evaluate_subscription_access([row]).has_access is False


@pytest.mark.asyncio
async def test_a_bare_play_snapshot_updates_the_newest_of_two_base_plan_rows(
    app_sessions,
):
    """Two Play rows of one subscription a legacy handler wrote with their
    base plans ('annual:p1m' bought first, 'annual:p1y' later) both match
    the bare 'annual' a snapshot names, on the same store. The newest takes
    the facts whatever order PostgreSQL returns them in; the other is left
    non-granting."""
    user = await _user(app_sessions, "play")
    now = datetime.now(UTC).replace(microsecond=0)
    async with app_sessions() as session:
        # The older row is stored first.
        for row_id, product_id, bought_days_ago in (
            ("play-older", "annual:p1m", 300),
            ("play-newer", "annual:p1y", 10),
        ):
            session.add(
                Subscription(
                    id=row_id,
                    user_id=user.id,
                    revenuecat_subscriber_id=user.firebase_uid,
                    provider="revenuecat",
                    product_id=product_id,
                    platform="android",
                    status="active",
                    purchased_at=now - timedelta(days=bought_days_ago),
                    expires_at=now + timedelta(days=30),
                    period_type="normal",
                    provider_snapshot_at=now - timedelta(days=1),
                    is_sandbox=False,
                )
            )
        await session.commit()
    body = standard_annual_response(
        request_date=now, expires_date=now + timedelta(days=355), store="play_store"
    )

    async with app_sessions() as session:
        outcome = await sync_revenuecat_access(
            session,
            await session.get(User, user.id),
            snapshot=RevenueCatSnapshot(
                body["subscriber"], now, app_user_id=user.firebase_uid
            ),
        )
        await session.commit()

    assert (outcome.result, outcome.row_created) == ("applied", False)
    rows = {row.id: row for row in await _rows(app_sessions, user)}
    assert (rows["play-newer"].product_id, rows["play-newer"].status) == (
        "annual",
        "active",
    )
    assert rows["play-newer"].expires_at == now + timedelta(days=355)
    assert (rows["play-older"].product_id, rows["play-older"].status) == (
        "annual:p1m",
        "expired",
    )


@pytest.mark.asyncio
async def test_a_refund_whose_read_failed_keeps_paid_routes_closed_until_repair(
    app_sessions,
):
    """A refunded annual payer whose refund CANCELLATION meets a RevenueCat
    outage: the cancellation handler leaves a paid-through 'cancelled' row,
    but the refund is remembered on it, so the paid-route fallback refuses
    it. The repair's snapshot then decides and clears the marker."""
    buyer = await _user(app_sessions, "refunded")
    now = datetime.now(UTC).replace(microsecond=0)
    async with app_sessions() as session:
        session.add(
            Subscription(
                user_id=buyer.id,
                revenuecat_subscriber_id=buyer.firebase_uid,
                provider="revenuecat",
                product_id="annual",
                platform="ios",
                status="active",
                purchased_at=now - timedelta(days=30),
                expires_at=now + timedelta(days=335),
                period_type="normal",
                provider_snapshot_at=now - timedelta(days=1),
                is_sandbox=False,
            )
        )
        await session.commit()

    async def down():
        return 503, None

    enforcement = CheckSubscriptionAccessQueryHandler(uow_factory=AsyncUnitOfWork)
    event = _event(
        "CANCELLATION",
        buyer.firebase_uid,
        cancel_reason="CUSTOMER_SUPPORT",
        event_timestamp_ms=int(now.timestamp() * 1000),
    )
    with _revenuecat({buyer.firebase_uid: down}):
        assert await _deliver(event) == {"status": "success"}

    (row,) = await _rows(app_sessions, buyer)
    assert (row.status, row.provider_sync_required_at) == ("cancelled", now)
    assert row.provider_refund_pending_at == now
    assert await enforcement.handle(CheckSubscriptionAccessQuery(buyer.id)) is False

    refunded = _annual(now + timedelta(seconds=5), timedelta(minutes=-1))
    with _revenuecat({buyer.firebase_uid: _ok(refunded)}):
        repaired = await RevenueCatAccessRefreshService(
            lambda: RevenueCatAdapter(api_key="test-key")
        ).refresh(buyer.id)

    assert repaired.result == "applied"
    (row,) = await _rows(app_sessions, buyer)
    assert (row.provider_sync_required_at, row.provider_refund_pending_at) == (
        None,
        None,
    )
    assert row.status == "expired"
    assert await enforcement.handle(CheckSubscriptionAccessQuery(buyer.id)) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_monthly", "event_monthly"),
    [("onemonth", "onemonth:p1m"), ("onemonth:p1m", "onemonth")],
)
async def test_an_old_play_products_expiration_during_an_outage_keeps_the_upgrade(
    app_sessions, stored_monthly, event_monthly
):
    """The Android variant on the real repository: the Play webhook's
    '<subscription>:<base plan>' and the stored bare id (or the reverse) are
    the same product, so the EXPIRATION lands on the monthly row."""
    user = await _user(app_sessions, "upgrader")
    now = datetime.now(UTC).replace(microsecond=0)
    async with app_sessions() as session:
        for product_id, status, purchased_days_ago, expires_in_days in (
            (stored_monthly, "expired", 400, -2),
            ("annual", "active", 2, 363),
        ):
            session.add(
                Subscription(
                    user_id=user.id,
                    revenuecat_subscriber_id=user.firebase_uid,
                    provider="revenuecat",
                    product_id=product_id,
                    platform="android",
                    status=status,
                    purchased_at=now - timedelta(days=purchased_days_ago),
                    expires_at=now + timedelta(days=expires_in_days),
                    period_type="normal",
                    provider_snapshot_at=now - timedelta(days=1),
                    is_sandbox=False,
                )
            )
        await session.commit()

    async def down():
        return 503, None

    with _revenuecat({user.firebase_uid: down}):
        result = await _deliver(
            _event(
                "EXPIRATION",
                user.firebase_uid,
                product_id=event_monthly,
                store="PLAY_STORE",
            )
        )

    assert result == {"status": "success"}
    rows = {row.product_id: row for row in await _rows(app_sessions, user)}
    assert rows["annual"].status == "active"
    assert rows["annual"].expires_at == now + timedelta(days=363)
    assert rows[stored_monthly].status == "expired"
    enforcement = CheckSubscriptionAccessQueryHandler(uow_factory=AsyncUnitOfWork)
    assert await enforcement.handle(CheckSubscriptionAccessQuery(user.id)) is True
