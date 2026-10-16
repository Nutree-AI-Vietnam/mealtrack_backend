"""Account deletion vs the entitlement ledger on real Postgres (NM-680, D11).

Deletion must never depend on the dark-launched ledger tables, and must
serialise with an in-flight entitlement sync through the same user lock.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from tests.fixtures.fakes.fake_billing_provider import make_sub, snapshot

from src.app.commands.user import DeleteUserCommand
from src.app.handlers.command_handlers.delete_user_command_handler import (
    DeleteUserCommandHandler,
)
from src.app.services.entitlement_sync_service import (
    USER_NOT_FOUND,
    EntitlementSyncService,
)
from src.domain.model.billing.snapshot import CustomerSnapshot
from src.domain.ports.billing_provider_port import BillingProviderPort
from src.infra.database.base import Base
from src.infra.database.models.billing import (
    BillingCustomerIdentityORM,
    StoreSubscriptionORM,
)
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.repositories.billing import (
    AsyncBillingCustomerIdentityRepository,
    AsyncUserEntitlementRepository,
)
from src.infra.services.rc_budget import RcBudget

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

LEDGER_TABLES = (
    "user_entitlements",
    "store_subscriptions",
    "entitlement_grants",
    "billing_provider_events",
    "billing_customer_identities",
)


@pytest.fixture
def real_uow(monkeypatch, async_session_factory):
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    return AsyncUnitOfWork


@pytest.fixture
def publisher():
    publisher = MagicMock()
    publisher.publish = AsyncMock()
    return publisher


async def _user(session, firebase_uid: str) -> str:
    user_id = str(uuid4())
    session.add(
        User(
            id=user_id,
            firebase_uid=firebase_uid,
            email=f"{user_id}@example.test",
            username=user_id[:20],
            password_hash="test",
            is_active=True,
        )
    )
    await session.commit()
    return user_id


async def _user_row(session, user_id: str):
    session.expire_all()
    return (
        await session.execute(
            select(User.is_active, User.email, User.deleted_at).where(
                User.id == user_id
            )
        )
    ).one()


async def _identity(session, customer_id: str):
    session.expire_all()
    return (
        await session.execute(
            select(BillingCustomerIdentityORM).where(
                BillingCustomerIdentityORM.provider_customer_id == customer_id
            )
        )
    ).scalar_one()


def _delete_handler(publisher) -> DeleteUserCommandHandler:
    return DeleteUserCommandHandler(uow=AsyncUnitOfWork(), event_publisher=publisher)


async def test_deletion_succeeds_without_the_ledger_tables(
    pg_session, real_uow, migrated_database, publisher
):
    """Code shipped before the manual migration, or after its downgrade()."""
    user_id = await _user(pg_session, "fb-no-ledger")
    for table in LEDGER_TABLES:
        await pg_session.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    await pg_session.commit()
    try:
        result = await _delete_handler(publisher).handle(
            DeleteUserCommand(firebase_uid="fb-no-ledger")
        )

        assert result["deleted"] is True
        row = await _user_row(pg_session, user_id)
        assert row.is_active is False
        assert row.email == f"deleted_{user_id}@deleted.local"
        assert row.deleted_at is not None
        publisher.publish.assert_awaited_once()
    finally:
        engine = create_engine(
            migrated_database.replace("postgresql+asyncpg", "postgresql+psycopg2")
        )
        Base.metadata.create_all(
            engine, tables=[Base.metadata.tables[name] for name in LEDGER_TABLES]
        )
        engine.dispose()


class DeletingProvider(BillingProviderPort):
    """RevenueCat answers while the account deletion commits."""

    provider = "revenuecat"

    def __init__(self, publisher):
        self.publisher = publisher
        self.calls: list[str] = []

    async def fetch_customer(self, customer_id: str) -> CustomerSnapshot:
        self.calls.append(customer_id)
        await _delete_handler(self.publisher).handle(
            DeleteUserCommand(firebase_uid=customer_id)
        )
        now = datetime.now(UTC)
        return snapshot(
            customer_id,
            make_sub(customer_id, expires_at=now + timedelta(days=30)),
            fetched_at=now,
        )


async def test_sync_in_flight_during_deletion_writes_nothing(
    pg_session, real_uow, publisher
):
    user_id = await _user(pg_session, "fb-deleting")
    await AsyncBillingCustomerIdentityRepository(pg_session).upsert_primary(
        user_id, "revenuecat", "fb-deleting", seen_at=datetime.now(UTC)
    )
    await pg_session.commit()
    provider = DeletingProvider(publisher)
    sync = EntitlementSyncService(
        uow_factory=real_uow,
        provider=provider,
        budget=RcBudget(rate_per_second=1000.0, concurrency=8),
    )

    result = await sync.sync_user(user_id, "webhook", "event:RENEWAL")

    assert provider.calls == ["fb-deleting"]
    assert result.sync_error == USER_NOT_FOUND
    assert (await _user_row(pg_session, user_id)).is_active is False
    identity = await _identity(pg_session, "fb-deleting")
    assert identity.retired_at is not None, "sync un-retired the deleted account"
    pg_session.expire_all()
    stored = await pg_session.execute(
        select(StoreSubscriptionORM).where(StoreSubscriptionORM.user_id == user_id)
    )
    assert stored.scalars().all() == []
    projection = await AsyncUserEntitlementRepository(pg_session).get(user_id)
    assert projection is None


async def test_deletion_waits_for_a_sync_holding_the_user_lock(
    pg_session, real_uow, async_session_factory, publisher
):
    user_id = await _user(pg_session, "fb-locked")
    async with async_session_factory() as sync_session:
        # A sync's apply transaction: user lock taken, identity written, not
        # yet committed.
        await AsyncUserEntitlementRepository(sync_session).lock_users([user_id])
        await AsyncBillingCustomerIdentityRepository(sync_session).upsert_primary(
            user_id, "revenuecat", "fb-locked", seen_at=datetime.now(UTC)
        )
        deletion = asyncio.create_task(
            _delete_handler(publisher).handle(
                DeleteUserCommand(firebase_uid="fb-locked")
            )
        )
        # Let the deletion run up to the point where it has to wait.
        await asyncio.sleep(0.5)
        assert not deletion.done()
        await sync_session.commit()

    await asyncio.wait_for(deletion, timeout=10)

    # Without the lock the deletion retires identities before the sync's
    # commit is visible and this one stays live (it waits later, on the
    # users row, after the ledger step).
    identity = await _identity(pg_session, "fb-locked")
    assert identity.retired_at is not None, "sync's identity survived deletion"
    assert (await _user_row(pg_session, user_id)).is_active is False
