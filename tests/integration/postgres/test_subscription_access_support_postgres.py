"""Support API reads and resync on real PostgreSQL.

Runs the real repository SQL, unit of work, RevenueCat adapter parser,
refresher, access sync and policy through the support handlers. Only the
adapter's HTTP transport is stubbed, with the documented v1 response shape.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.revenuecat_samples import (
    empty_customer_response,
    patched_revenuecat_http,
    standard_annual_response,
)

from src.app.commands.subscription_access import ResyncSubscriptionAccessCommand
from src.app.handlers.command_handlers.resync_subscription_access_command_handler import (
    ResyncSubscriptionAccessCommandHandler,
)
from src.app.handlers.query_handlers.get_support_subscription_access_query_handler import (
    GetSupportSubscriptionAccessQueryHandler,
)
from src.app.handlers.query_handlers.list_unresolved_subscription_access_query_handler import (
    ListUnresolvedSubscriptionAccessQueryHandler,
)
from src.app.queries.subscription_access import (
    GetSupportSubscriptionAccessQuery,
    ListUnresolvedSubscriptionAccessQuery,
)
from src.domain.ports.subscription_service_port import RevenueCatSnapshotUnavailable
from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.repositories.subscription_access_support_repository import (
    SubscriptionAccessSupportRepository,
    unresolved_users_statement,
)
from src.infra.services.subscription_access_refresh_service import (
    RevenueCatAccessRefreshService,
)

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC).replace(microsecond=0)


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


async def _user(
    factory, *, email: str | None = None, active: bool = True, deleted: bool = False
) -> User:
    user = User(
        id=str(uuid4()),
        firebase_uid=f"uid-{uuid4()}",
        email=email or f"buyer-{uuid4()}@example.com",
        username=f"buyer-{uuid4()}",
        password_hash="unused",
        is_active=active,
        deleted_at=NOW if deleted else None,
    )
    async with factory() as session:
        session.add(user)
        await session.commit()
    return user


async def _subscription(factory, user: User, **values) -> Subscription:
    facts = {
        "id": str(uuid4()),
        "user_id": user.id,
        "provider": "revenuecat",
        "revenuecat_subscriber_id": user.firebase_uid,
        "product_id": "annual",
        "platform": "ios",
        "status": "active",
        "purchased_at": NOW - timedelta(days=30),
        "expires_at": NOW + timedelta(days=335),
        "provider_snapshot_at": NOW - timedelta(days=2),
        "is_sandbox": False,
    }
    row = Subscription(**(facts | values))
    async with factory() as session:
        session.add(row)
        await session.commit()
    return row


async def _rows(factory, user: User) -> list[Subscription]:
    async with factory() as session:
        return list(
            (
                await session.scalars(
                    select(Subscription)
                    .where(Subscription.user_id == user.id)
                    .order_by(Subscription.purchased_at)
                )
            ).all()
        )


def _refresher() -> RevenueCatAccessRefreshService:
    return RevenueCatAccessRefreshService(lambda: RevenueCatAdapter(api_key="test-key"))


def _lookup_handler() -> GetSupportSubscriptionAccessQueryHandler:
    return GetSupportSubscriptionAccessQueryHandler(
        SubscriptionAccessSupportRepository(), _refresher()
    )


def _resync_handler() -> ResyncSubscriptionAccessCommandHandler:
    return ResyncSubscriptionAccessCommandHandler(
        SubscriptionAccessSupportRepository(), _refresher()
    )


# --- Lookup -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_finds_a_user_by_id_firebase_uid_or_email(app_sessions):
    user = await _user(app_sessions, email="buyer@example.com")
    await _user(app_sessions)  # someone else
    repository = SubscriptionAccessSupportRepository()

    for query in (
        user.id,
        user.firebase_uid,
        "buyer@example.com",
        " buyer@example.com ",
    ):
        (found,) = await repository.find_users(query, limit=5)
        assert (found.id, found.firebase_uid, found.email) == (
            user.id,
            user.firebase_uid,
            "buyer@example.com",
        )


@pytest.mark.asyncio
async def test_lookup_retries_an_email_ignoring_case_only_on_a_miss(app_sessions):
    lower = await _user(app_sessions, email="mixed@example.com")
    upper = await _user(app_sessions, email="MIXED@example.com")
    repository = SubscriptionAccessSupportRepository()

    (exact,) = await repository.find_users("MIXED@example.com", limit=5)
    folded = await repository.find_users("Mixed@Example.com", limit=5)

    assert exact.id == upper.id
    assert {user.id for user in folded} == {lower.id, upper.id}


@pytest.mark.asyncio
async def test_lookup_of_nobody_finds_nothing(app_sessions):
    await _user(app_sessions)
    repository = SubscriptionAccessSupportRepository()

    assert await repository.find_users("nobody@example.com", limit=5) == []
    assert await repository.find_users(str(uuid4()), limit=5) == []
    assert await repository.find_users("   ", limit=5) == []
    assert await repository.get_user(str(uuid4())) is None


@pytest.mark.asyncio
async def test_lookup_includes_deleted_accounts(app_sessions):
    deleted = await _user(app_sessions, active=False, deleted=True)
    repository = SubscriptionAccessSupportRepository()

    (found,) = await repository.find_users(deleted.id, limit=5)

    assert (found.is_active, found.is_live) == (False, False)
    assert found.deleted_at is not None


@pytest.mark.asyncio
async def test_lookup_returns_every_provider_row_and_the_decision(app_sessions):
    user = await _user(app_sessions)
    await _subscription(app_sessions, user)
    await _subscription(
        app_sessions,
        user,
        provider="momo",
        revenuecat_subscriber_id=None,
        product_id="momo_pass_90d",
        platform="web",
        purchased_at=NOW - timedelta(days=5),
        expires_at=NOW + timedelta(days=85),
    )

    view = await _lookup_handler().handle(GetSupportSubscriptionAccessQuery(user.email))

    assert [row.provider for row in view.subscriptions] == ["momo", "revenuecat"]
    assert view.decision.outcome == "granted"
    assert (view.decision.provider, view.decision.product_id) == (
        "revenuecat",
        "annual",
    )
    assert view.live is None


# --- Live read ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_lookup_reads_revenuecat_and_writes_nothing(app_sessions):
    user = await _user(app_sessions)
    stored = await _subscription(
        app_sessions,
        user,
        status="cancelled",
        provider_sync_required_at=NOW - timedelta(hours=1),
    )
    body = standard_annual_response(
        request_date=NOW, expires_date=NOW + timedelta(days=200)
    )

    with patched_revenuecat_http(body) as get:
        view = await _lookup_handler().handle(
            GetSupportSubscriptionAccessQuery(user.id, live=True)
        )

    assert get.await_count == 1
    assert view.live is not None and view.live.status == "ok"
    assert view.live.peek is not None and view.live.peek.standard is not None
    assert view.live.peek.standard.expires_at == NOW + timedelta(days=200)
    # Unchanged: same facts, marker still set, no row added.
    (row,) = await _rows(app_sessions, user)
    assert (row.status, row.expires_at, row.provider_snapshot_at) == (
        "cancelled",
        stored.expires_at,
        stored.provider_snapshot_at,
    )
    assert row.provider_sync_required_at == stored.provider_sync_required_at
    assert view.decision.outcome == "unresolved"


@pytest.mark.asyncio
async def test_plain_lookup_never_calls_revenuecat(app_sessions):
    user = await _user(app_sessions)

    with patched_revenuecat_http(empty_customer_response()) as get:
        await _lookup_handler().handle(GetSupportSubscriptionAccessQuery(user.id))

    get.assert_not_awaited()


# --- Resync -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resync_applies_and_clears_the_marker(app_sessions):
    user = await _user(app_sessions)
    await _subscription(
        app_sessions,
        user,
        status="cancelled",
        provider_sync_required_at=NOW - timedelta(hours=1),
    )
    body = standard_annual_response(
        request_date=NOW, expires_date=NOW + timedelta(days=200)
    )

    with patched_revenuecat_http(body):
        outcome = await _resync_handler().handle(
            ResyncSubscriptionAccessCommand(user.id)
        )

    assert (outcome.sync.result, outcome.sync.markers_cleared) == ("applied", 1)
    assert outcome.before.outcome == "unresolved"
    assert outcome.after.outcome == "granted"
    (row,) = await _rows(app_sessions, user)
    assert (row.status, row.provider_sync_required_at) == ("active", None)
    assert row.provider_snapshot_at == NOW


@pytest.mark.asyncio
async def test_resync_of_a_transfer_source_applies_the_emptied_customer(app_sessions):
    """RevenueCat moved the purchases away while the webhook's read failed;
    the remembered transfer makes the empty customer authoritative, exactly
    as reconcile --repair applies it."""
    user = await _user(app_sessions)
    marked_at = NOW - timedelta(hours=3)
    await _subscription(
        app_sessions,
        user,
        provider_sync_required_at=marked_at,
        provider_transfer_source_at=marked_at,
    )

    with patched_revenuecat_http(
        empty_customer_response(request_date=NOW, app_user_id=user.firebase_uid)
    ):
        outcome = await _resync_handler().handle(
            ResyncSubscriptionAccessCommand(user.id)
        )

    assert outcome.before.unresolved_reasons == ["sync_required", "transfer_source"]
    # Applied as "no purchases": the row is revoked, not left unresolved.
    assert (outcome.sync.result, outcome.sync.rows_revoked) == ("applied", 1)
    assert outcome.sync.markers_cleared == 1
    assert outcome.after.outcome == "denied"
    (row,) = await _rows(app_sessions, user)
    assert row.status == "expired"
    assert (row.provider_sync_required_at, row.provider_transfer_source_at) == (
        None,
        None,
    )


@pytest.mark.asyncio
async def test_resync_never_expires_a_possible_grant_without_a_transfer(app_sessions):
    user = await _user(app_sessions)
    await _subscription(
        app_sessions, user, provider_sync_required_at=NOW - timedelta(hours=3)
    )

    with patched_revenuecat_http(
        empty_customer_response(request_date=NOW, app_user_id=user.firebase_uid)
    ):
        outcome = await _resync_handler().handle(
            ResyncSubscriptionAccessCommand(user.id)
        )

    assert (outcome.sync.result, outcome.sync.reason) == (
        "not_applied",
        "absent_with_history",
    )
    (row,) = await _rows(app_sessions, user)
    assert (row.status, row.provider_sync_required_at) == (
        "active",
        NOW - timedelta(hours=3),
    )
    assert outcome.after.outcome == "unresolved"


@pytest.mark.asyncio
async def test_resync_while_revenuecat_is_down_writes_nothing(app_sessions):
    user = await _user(app_sessions)
    stored = await _subscription(
        app_sessions, user, provider_sync_required_at=NOW - timedelta(hours=1)
    )

    with (
        patched_revenuecat_http(None, status_code=503),
        pytest.raises(RevenueCatSnapshotUnavailable) as raised,
    ):
        await _resync_handler().handle(ResyncSubscriptionAccessCommand(user.id))

    assert raised.value.reason == "http_5xx"
    (row,) = await _rows(app_sessions, user)
    assert row.provider_sync_required_at == stored.provider_sync_required_at


# --- Repair queue -------------------------------------------------------------


async def _queue(limit: int, after=None):
    handler = ListUnresolvedSubscriptionAccessQueryHandler(
        SubscriptionAccessSupportRepository()
    )
    return await handler.handle(ListUnresolvedSubscriptionAccessQuery(limit, after))


@pytest.mark.asyncio
async def test_queue_lists_live_marked_users_oldest_marker_first(app_sessions):
    oldest = await _user(app_sessions)
    await _subscription(
        app_sessions,
        oldest,
        provider_sync_required_at=NOW - timedelta(hours=5),
        provider_refund_pending_at=NOW - timedelta(hours=5),
    )
    # A second marked row: each type reports its oldest time for the user.
    await _subscription(
        app_sessions,
        oldest,
        purchased_at=NOW - timedelta(days=400),
        provider_sync_required_at=NOW - timedelta(hours=7),
    )
    transfer = await _user(app_sessions)
    await _subscription(
        app_sessions,
        transfer,
        provider_sync_required_at=NOW - timedelta(hours=2),
        provider_transfer_source_at=NOW - timedelta(hours=2),
    )
    newest = await _user(app_sessions)
    await _subscription(
        app_sessions, newest, provider_sync_required_at=NOW - timedelta(minutes=5)
    )
    # Never listed: unmarked rows, deleted accounts, other providers.
    await _subscription(app_sessions, await _user(app_sessions))
    deleted = await _user(app_sessions, active=False, deleted=True)
    await _subscription(
        app_sessions, deleted, provider_sync_required_at=NOW - timedelta(days=9)
    )
    momo = await _user(app_sessions)
    await _subscription(
        app_sessions,
        momo,
        provider="momo",
        revenuecat_subscriber_id=None,
        provider_sync_required_at=NOW - timedelta(days=8),
    )

    page = await _queue(10)

    assert [item.user_id for item in page.items] == [oldest.id, transfer.id, newest.id]
    first = page.items[0]
    assert first.firebase_uid == oldest.firebase_uid
    assert first.oldest_marker_at == NOW - timedelta(hours=7)
    assert first.markers() == [
        ("sync_required", NOW - timedelta(hours=7)),
        ("refund_pending", NOW - timedelta(hours=5)),
    ]
    assert page.items[1].markers() == [
        ("sync_required", NOW - timedelta(hours=2)),
        ("transfer_source", NOW - timedelta(hours=2)),
    ]
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_queue_pages_through_every_user_once_with_tied_marker_times(
    app_sessions,
):
    marked_at = NOW - timedelta(hours=1)
    users = [await _user(app_sessions) for _ in range(5)]
    for user in users:
        await _subscription(app_sessions, user, provider_sync_required_at=marked_at)
    older = await _user(app_sessions)
    await _subscription(
        app_sessions, older, provider_sync_required_at=marked_at - timedelta(seconds=1)
    )

    seen, after, pages = [], None, 0
    while True:
        page = await _queue(2, after)
        pages += 1
        seen.extend(item.user_id for item in page.items)
        if page.next_cursor is None:
            break
        after = page.next_cursor

    assert pages == 3
    assert seen == [older.id, *sorted(user.id for user in users)]


async def explain(session: AsyncSession, statement) -> str:
    """PostgreSQL's plan for a statement, with sequential scans priced out
    (the test tables are tiny, so the planner would scan them anyway)."""
    compiled = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    rows = (await session.execute(text(f"EXPLAIN {compiled}"))).all()
    return "\n".join(row[0] for row in rows)


@pytest.mark.asyncio
async def test_the_queue_query_can_use_the_partial_index(app_sessions):
    """Its predicate implies ix_subscriptions_access_markers', so PostgreSQL
    can read only the marked rows instead of every subscription."""
    async with app_sessions() as session:
        plan = await explain(session, unresolved_users_statement(limit=51, after=None))

    assert "ix_subscriptions_access_markers" in plan
