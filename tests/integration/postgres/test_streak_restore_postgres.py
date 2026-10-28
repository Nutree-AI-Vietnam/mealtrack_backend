"""Streak restore ledger and GET /v1/meals/streak on real PostgreSQL."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exceptions import ConflictException
from src.api.routes.v1.meals_read import router as meals_read_router
from src.app.commands.streak import PackClaim, RestoreStreakCommand
from src.app.handlers.command_handlers.restore_streak_command_handler import (
    RestoreStreakCommandHandler,
)
from src.app.handlers.query_handlers.get_streak_query_handler import (
    GetStreakQueryHandler,
)
from src.app.queries.meal.get_streak_query import GetStreakQuery
from src.app.services.streak_restore_ledger import (
    PackPurchase,
    credit_pack,
    refund_pack,
    spend_restores,
)
from src.domain.model.streak import (
    LedgerEntry,
    LedgerKind,
    NotEnoughRestoresError,
    StreakRestorePolicy,
)
from src.domain.ports.subscription_service_port import (
    OneTimePurchase,
    OneTimePurchaseLookup,
)
from src.infra.database.models.enums import MealStatusEnum
from src.infra.database.models.meal.meal import MealORM
from src.infra.database.models.streak_restore import StreakRestoreLedgerORM
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.event_bus.pymediator_event_bus import PyMediatorEventBus

pytestmark = pytest.mark.integration

ZONE = "Asia/Ho_Chi_Minh"
DAY_1, DAY_2, DAY_3 = date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)


@pytest_asyncio.fixture
async def factory(monkeypatch, pg_session, async_session_factory):
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    return async_session_factory


async def _user(session, timezone: str = ZONE) -> str:
    user = User(
        id=str(uuid4()),
        firebase_uid=f"uid-{uuid4()}",
        email=f"{uuid4()}@example.com",
        username=f"user-{uuid4()}",
        password_hash="unused",
        timezone=timezone,
    )
    session.add(user)
    await session.commit()
    return user.id


async def _meals(session, user_id: str, *created_at: datetime) -> None:
    for moment in created_at:
        session.add(
            MealORM(
                meal_id=str(uuid4()),
                user_id=user_id,
                status=MealStatusEnum.PROCESSING,
                created_at=moment,
                updated_at=moment,
            )
        )
    await session.commit()


def _pack(count: int = 3, transaction: str = "tx-1") -> PackPurchase:
    return PackPurchase(
        product_id=f"nutree_streak_restore_{count}",
        store_transaction_id=transaction,
        count=count,
        store="app_store",
        environment="production",
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


@pytest.mark.asyncio
async def test_table_and_indexes_exist_after_init(factory):
    async with factory() as session:
        indexes = set(
            (
                await session.execute(
                    text(
                        "SELECT indexname FROM pg_indexes"
                        " WHERE tablename = 'streak_restore_ledger'"
                    )
                )
            ).scalars()
        )
    assert {
        "uq_streak_restore_ledger_restored_day",
        "uq_streak_restore_ledger_one_free",
        "uq_streak_restore_ledger_transaction",
    } <= indexes


@pytest.mark.asyncio
async def test_one_free_restore_and_one_row_per_restored_day(factory, pg_session):
    user_id = await _user(pg_session)
    free = LedgerEntry(user_id, LedgerKind.FREE, 0, restored_date=DAY_1)
    async with AsyncUnitOfWork() as uow:
        assert await uow.streak_restores.insert_entry(free)
        same_day = LedgerEntry(user_id, LedgerKind.SPEND, -1, restored_date=DAY_1)
        assert await uow.streak_restores.insert_entry(same_day) is False

    with pytest.raises(IntegrityError):
        async with AsyncUnitOfWork() as uow:
            second_free = LedgerEntry(user_id, LedgerKind.FREE, 0, restored_date=DAY_2)
            await uow.streak_restores.insert_entry(second_free)

    assert len(await _rows(factory, user_id)) == 1


@pytest.mark.asyncio
async def test_a_transaction_credited_twice_leaves_one_row(factory, pg_session):
    user_id = await _user(pg_session)
    for _ in range(2):
        async with AsyncUnitOfWork() as uow:
            await credit_pack(uow, user_id, _pack())
    assert len(await _rows(factory, user_id)) == 1
    assert await _balance(user_id) == 3


@pytest.mark.asyncio
async def test_refund_before_credit_nets_zero(factory, pg_session):
    user_id = await _user(pg_session)
    async with AsyncUnitOfWork() as uow:
        await refund_pack(uow, user_id, _pack())
    async with AsyncUnitOfWork() as uow:
        claim = await credit_pack(uow, user_id, _pack())

    assert claim.inserted is False
    kinds = sorted(row.kind for row in await _rows(factory, user_id))
    assert kinds == ["purchase", "refund"]
    assert await _balance(user_id) == 0


@pytest.mark.asyncio
async def test_refund_after_spend_floors_the_balance_at_zero(factory, pg_session):
    user_id = await _user(pg_session)
    async with AsyncUnitOfWork() as uow:
        await credit_pack(uow, user_id, _pack(count=1))
    async with AsyncUnitOfWork() as uow:
        await spend_restores(uow, user_id, [DAY_1, DAY_2])  # free + paid
    async with AsyncUnitOfWork() as uow:
        refund = await refund_pack(uow, user_id, _pack(count=1))

    assert refund.count == 0
    assert await _balance(user_id) == 0


@pytest.mark.asyncio
async def test_two_concurrent_spends_of_one_restore_restore_one_day(
    factory, pg_session
):
    user_id = await _user(pg_session)
    async with AsyncUnitOfWork() as uow:
        await spend_restores(uow, user_id, [DAY_1])  # the free one
        await credit_pack(uow, user_id, _pack(count=1))

    async def spend(day: date) -> list[date] | None:
        try:
            async with AsyncUnitOfWork() as uow:
                return await spend_restores(uow, user_id, [day])
        except NotEnoughRestoresError:
            return None

    results = await asyncio.gather(spend(DAY_2), spend(DAY_3))

    assert sorted(result is None for result in results) == [False, True]
    assert await _balance(user_id) == 0
    spent = [row for row in await _rows(factory, user_id) if row.kind == "spend"]
    assert len(spent) == 1


@pytest.mark.asyncio
async def test_streak_log_days_read_the_noon_stamp_on_the_users_clock(
    factory, pg_session
):
    user_id = await _user(pg_session)
    await _meals(
        pg_session,
        user_id,
        datetime(2026, 10, 20, 1, 15, tzinfo=UTC),  # 08:15 in Saigon
        datetime(2026, 10, 21, 5, 0, tzinfo=UTC),  # noon in Saigon
        datetime(2026, 10, 22, 5, 0, tzinfo=UTC),  # noon in Saigon
        datetime(2026, 10, 22, 2, 0, tzinfo=UTC),  # 09:00 in Saigon
    )

    async with AsyncUnitOfWork() as uow:
        saigon = await uow.meals.get_streak_log_days(user_id, ZONE)
        los_angeles = await uow.meals.get_streak_log_days(
            user_id, "America/Los_Angeles"
        )

    assert saigon == [(DAY_3, True), (DAY_2, False), (DAY_1, True)]
    # The same instants are not noon in Los Angeles.
    assert los_angeles == [
        (DAY_2, True),
        (DAY_1, True),
        (DAY_1 - timedelta(days=1), True),
    ]


def _app(user_id: str) -> FastAPI:
    bus = PyMediatorEventBus()
    bus.register_handler(
        GetStreakQuery, GetStreakQueryHandler(policy=StreakRestorePolicy())
    )
    app = FastAPI()
    app.include_router(meals_read_router, prefix="/v1/meals")
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    return app


@pytest.mark.asyncio
async def test_get_streak_returns_the_offer_then_the_restored_day(factory, pg_session):
    user_id = await _user(pg_session)
    today = datetime.now(ZoneInfo(ZONE)).date()
    yesterday = today - timedelta(days=1)
    await _meals(
        pg_session,
        user_id,
        *(
            datetime.combine(
                today - timedelta(days=offset),
                datetime.min.time().replace(hour=9),
                tzinfo=ZoneInfo(ZONE),
            )
            for offset in range(2, 14)
        ),
    )
    transport = httpx.ASGITransport(app=_app(user_id))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        before = (await api.get("/v1/meals/streak")).json()
        async with AsyncUnitOfWork() as uow:
            await spend_restores(uow, user_id, [yesterday])
        after = (await api.get("/v1/meals/streak")).json()

    assert before["current_streak"] == 0
    assert before["best_streak"] == 12
    assert before["restore"] == {
        "balance": 0,
        "free_available": True,
        "missed_dates": [yesterday.isoformat()],
        "restorable_until": (yesterday + timedelta(days=2)).isoformat(),
        "broken_streak": 12,
        "restored_dates": [],
    }
    assert after["current_streak"] == 12
    assert after["restore"]["free_available"] is False
    assert after["restore"]["missed_dates"] == []
    assert after["restore"]["restored_dates"] == [yesterday.isoformat()]


def _restore_handler() -> RestoreStreakCommandHandler:
    subscriptions = MagicMock()
    subscriptions.find_one_time_purchase = AsyncMock(
        return_value=OneTimePurchase(OneTimePurchaseLookup.FOUND, store="app_store")
    )
    return RestoreStreakCommandHandler(
        uow_factory=AsyncUnitOfWork,
        cache_service=None,
        subscription_service=subscriptions,
        policy=StreakRestorePolicy(),
        retry_delay_seconds=0,
    )


async def _twelve_days_then_a_miss(session, user_id: str) -> date:
    today = datetime.now(ZoneInfo(ZONE)).date()
    await _meals(
        session,
        user_id,
        *(
            datetime.combine(
                today - timedelta(days=offset),
                datetime.min.time().replace(hour=9),
                tzinfo=ZoneInfo(ZONE),
            )
            for offset in range(2, 14)
        ),
    )
    return today - timedelta(days=1)


@pytest.mark.asyncio
async def test_two_concurrent_restores_of_one_day_spend_once(factory, pg_session):
    user_id = await _user(pg_session)
    missed = await _twelve_days_then_a_miss(pg_session, user_id)
    async with AsyncUnitOfWork() as uow:
        await spend_restores(uow, user_id, [DAY_1])  # the free one, long ago
        await credit_pack(uow, user_id, _pack(count=1))
    handler = _restore_handler()
    command = RestoreStreakCommand(user_id=user_id, dates=[missed])

    results = await asyncio.gather(handler.handle(command), handler.handle(command))

    assert [result["outcome"] for result in results] == ["restored", "restored"]
    assert await _balance(user_id) == 0
    spent = [row for row in await _rows(factory, user_id) if row.kind == "spend"]
    assert [row.restored_date for row in spent] == [missed]


@pytest.mark.asyncio
async def test_a_transaction_claimed_twice_by_its_buyer_is_one_row(factory, pg_session):
    user_id = await _user(pg_session)
    handler = _restore_handler()
    command = RestoreStreakCommand(
        user_id=user_id, purchase=PackClaim("nutree_streak_restore_3", "tx-claim")
    )

    first = await handler.handle(command)
    second = await handler.handle(command)

    assert (first["credited"], second["credited"]) == (3, 3)
    assert len(await _rows(factory, user_id)) == 1
    assert await _balance(user_id) == 3


@pytest.mark.asyncio
async def test_a_transaction_claimed_by_another_user_is_refused(factory, pg_session):
    buyer = await _user(pg_session)
    other = await _user(pg_session)
    handler = _restore_handler()
    claim = PackClaim("nutree_streak_restore_3", "tx-shared")
    await handler.handle(RestoreStreakCommand(user_id=buyer, purchase=claim))

    with pytest.raises(ConflictException) as error:
        await handler.handle(RestoreStreakCommand(user_id=other, purchase=claim))

    assert error.value.error_code == "PURCHASE_OWNED_BY_ANOTHER_ACCOUNT"
    assert len(await _rows(factory, buyer)) == 1
    assert await _rows(factory, other) == []
