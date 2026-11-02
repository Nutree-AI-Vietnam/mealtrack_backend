"""/v1/supplements routes, the streak and the day feed on real PostgreSQL."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exception_handlers import register_exception_handlers
from src.api.routes.v1.activities import router as activities_router
from src.api.routes.v1.meals_read import router as meals_read_router
from src.api.routes.v1.supplements import require_supplements
from src.api.routes.v1.supplements import router as supplements_router
from src.app.commands.supplement import (
    AddSupplementCommand,
    DeleteSupplementIntakeCommand,
    LogSupplementIntakeCommand,
    RemoveSupplementCommand,
    UpdateSupplementCommand,
)
from src.app.handlers.command_handlers.supplement_command_handlers import (
    AddSupplementCommandHandler,
    DeleteSupplementIntakeCommandHandler,
    LogSupplementIntakeCommandHandler,
    RemoveSupplementCommandHandler,
    UpdateSupplementCommandHandler,
)
from src.app.handlers.query_handlers.get_daily_activities_query_handler import (
    GetDailyActivitiesQueryHandler,
)
from src.app.handlers.query_handlers.get_streak_query_handler import (
    GetStreakQueryHandler,
)
from src.app.handlers.query_handlers.supplement_query_handlers import (
    GetMySupplementsQueryHandler,
    LookupSupplementBarcodeQueryHandler,
    SearchSupplementsQueryHandler,
)
from src.app.queries.activity import GetDailyActivitiesQuery
from src.app.queries.meal.get_streak_query import GetStreakQuery
from src.app.queries.supplement import (
    GetMySupplementsQuery,
    LookupSupplementBarcodeQuery,
    SearchSupplementsQuery,
)
from src.domain.model.streak import StreakRestorePolicy
from src.domain.model.supplement import (
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementNutrients,
    SupplementProduct,
)
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.event_bus.pymediator_event_bus import PyMediatorEventBus

pytestmark = pytest.mark.integration

ZONE = "Asia/Ho_Chi_Minh"
BARCODE = "9300807362341"


@pytest_asyncio.fixture
async def factory(monkeypatch, pg_session, async_session_factory):
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    await pg_session.execute(text("TRUNCATE TABLE supplement_products CASCADE"))
    await pg_session.commit()
    yield async_session_factory
    await pg_session.execute(text("TRUNCATE TABLE supplement_products CASCADE"))
    await pg_session.commit()


async def _user(session) -> str:
    user = User(
        id=str(uuid4()),
        firebase_uid=f"uid-{uuid4()}",
        email=f"{uuid4()}@example.com",
        username=f"user-{uuid4()}",
        password_hash="unused",
        timezone=ZONE,
    )
    session.add(user)
    await session.commit()
    return user.id


async def _product(
    name: str, owner: str | None = None, **overrides
) -> SupplementProduct:
    product = SupplementProduct(
        **{
            "id": str(uuid4()),
            "name": name,
            "brand": "Blackmores",
            "form": SupplementForm.CAPSULE,
            "unit_label": "viên nang",
            "units_per_serving": 1,
            "source": ProductSource.CURATED
            if owner is None
            else ProductSource.LABEL_SCAN,
            "review_status": ReviewStatus.APPROVED
            if owner is None
            else ReviewStatus.PRIVATE,
            "owner_user_id": owner,
            "nutrients": SupplementNutrients({"vitamin_c": 500.0}),
            **overrides,
        }
    )
    async with AsyncUnitOfWork() as uow:
        await uow.supplements.add_product(product)
    return product


def _client(
    user_id: str, *, switched_on: bool = True, purged: list | None = None
) -> httpx.AsyncClient:
    bus = PyMediatorEventBus()
    for message, handler in (
        (AddSupplementCommand, AddSupplementCommandHandler),
        (UpdateSupplementCommand, UpdateSupplementCommandHandler),
        (RemoveSupplementCommand, RemoveSupplementCommandHandler),
        (LogSupplementIntakeCommand, LogSupplementIntakeCommandHandler),
        (DeleteSupplementIntakeCommand, DeleteSupplementIntakeCommandHandler),
    ):
        bus.register_handler(
            message, handler(uow_factory=AsyncUnitOfWork, purge_day=_recorder(purged))
        )
    for message, handler in (
        (GetMySupplementsQuery, GetMySupplementsQueryHandler),
        (SearchSupplementsQuery, SearchSupplementsQueryHandler),
        (LookupSupplementBarcodeQuery, LookupSupplementBarcodeQueryHandler),
    ):
        bus.register_handler(message, handler(uow_factory=AsyncUnitOfWork))
    bus.register_handler(
        GetStreakQuery, GetStreakQueryHandler(policy=StreakRestorePolicy())
    )
    bus.register_handler(GetDailyActivitiesQuery, GetDailyActivitiesQueryHandler())
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(supplements_router)
    app.include_router(meals_read_router, prefix="/v1/meals")
    app.include_router(activities_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    if switched_on:
        app.dependency_overrides[require_supplements] = lambda: None
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _recorder(purged: list | None):
    if purged is None:
        return None

    async def purge(user_id, day):
        purged.append((user_id, day))

    return purge


@pytest.mark.asyncio
async def test_routes_answer_503_while_switched_off(factory, pg_session, monkeypatch):
    monkeypatch.setattr(
        "src.bootstrap.supplements.get_settings",
        lambda: SimpleNamespace(SUPPLEMENTS_ENABLED=False),
    )
    async with _client(await _user(pg_session), switched_on=False) as api:
        response = await api.get("/v1/supplements/mine")

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "SUPPLEMENTS_UNAVAILABLE"


@pytest.mark.asyncio
async def test_add_log_and_undo_a_dose_shows_in_the_list_feed_and_streak(
    factory, pg_session
):
    alice = await _user(pg_session)
    product = await _product("Bio C 1000", units_per_serving=2)

    async with _client(alice) as api:
        added = await api.post(
            "/v1/supplements/mine",
            json={"product_id": product.id, "log_now": True, "intake_id": str(uuid4())},
        )
        assert added.status_code == 201, added.text
        body = added.json()
        assert body["entry"]["units_per_dose"] == 2
        assert body["intake"]["nutrients"] == {"vitamin_c": 1000.0}

        mine = (await api.get("/v1/supplements/mine")).json()
        [item] = mine["items"]
        assert item["product"]["name"] == "Bio C 1000"
        assert [d["id"] for d in item["doses"]] == [body["intake"]["id"]]

        feed = (await api.get("/v1/activities/daily")).json()
        assert [a["type"] for a in feed] == ["supplement"]
        streak = (await api.get("/v1/meals/streak")).json()
        assert streak["current_streak"] == 1

        undo = await api.delete(f"/v1/supplements/intakes/{body['intake']['id']}")
        assert undo.status_code == 200
        mine = (await api.get("/v1/supplements/mine")).json()
        assert mine["items"][0]["doses"] == []


@pytest.mark.asyncio
async def test_a_reused_intake_id_is_409_unless_the_idempotency_key_replays(
    factory, pg_session
):
    alice = await _user(pg_session)
    product = await _product("Fish Oil 1000mg")
    dose = {"intake_id": str(uuid4()), "product_id": product.id, "units": 2}

    async with _client(alice) as api:
        first = await api.post(
            "/v1/supplements/intakes", json=dose, headers={"Idempotency-Key": "k-1"}
        )
        replay = await api.post(
            "/v1/supplements/intakes", json=dose, headers={"Idempotency-Key": "k-1"}
        )
        reused = await api.post("/v1/supplements/intakes", json=dose)

    assert first.status_code == 201, first.text
    assert replay.status_code == 201 and replay.json() == first.json()
    assert reused.status_code == 409
    assert reused.json()["detail"]["error_code"] == "SUPPLEMENT_INTAKE_ID_TAKEN"


@pytest.mark.asyncio
async def test_another_users_rows_are_404_on_every_route_that_takes_an_id(
    factory, pg_session
):
    alice, bob = await _user(pg_session), await _user(pg_session)
    private = await _product("My scan", owner=alice, barcodes=(BARCODE,))
    async with _client(alice) as api:
        added = (
            await api.post(
                "/v1/supplements/mine",
                json={
                    "product_id": private.id,
                    "log_now": True,
                    "intake_id": str(uuid4()),
                },
            )
        ).json()

    async with _client(bob) as api:
        responses = [
            await api.get(f"/v1/supplements/barcode/{BARCODE}"),
            await api.post("/v1/supplements/mine", json={"product_id": private.id}),
            await api.post(
                "/v1/supplements/intakes",
                json={"intake_id": str(uuid4()), "product_id": private.id, "units": 1},
            ),
            await api.patch(
                f"/v1/supplements/mine/{added['entry']['id']}",
                json={"units_per_dose": 3},
            ),
            await api.delete(f"/v1/supplements/mine/{added['entry']['id']}"),
            await api.delete(f"/v1/supplements/intakes/{added['intake']['id']}"),
        ]
        search = (
            await api.get("/v1/supplements/search", params={"q": "my scan"})
        ).json()

    assert [r.status_code for r in responses] == [404] * 6
    assert search == {"items": []}


@pytest.mark.asyncio
async def test_search_ignores_accents_and_flags_macro_bearing_rows(factory, pg_session):
    alice = await _user(pg_session)
    oil = await _product("Dầu cá Omega 3", brand="Ostelin", sold_in_vn=True)
    shake = await _product(
        "Protein shake vị socola", brand="Ostelin", macro_bearing=True
    )

    async with _client(alice) as api:
        found = (await api.get("/v1/supplements/search", params={"q": "dau ca"})).json()
        shakes = (
            await api.get("/v1/supplements/search", params={"q": "SOCOLA"})
        ).json()

    assert [p["id"] for p in found["items"]] == [oil.id]
    assert shakes["items"][0]["id"] == shake.id
    assert shakes["items"][0]["macro_bearing"] is True


@pytest.mark.asyncio
async def test_a_backdated_dose_is_not_logged_on_its_day(factory, pg_session):
    alice = await _user(pg_session)
    product = await _product("Vitamin D3")
    yesterday = datetime.now(ZoneInfo(ZONE)) - timedelta(days=1)

    async with _client(alice) as api:
        logged = await api.post(
            "/v1/supplements/intakes",
            json={
                "intake_id": str(uuid4()),
                "product_id": product.id,
                "units": 1,
                "taken_at": yesterday.isoformat(),
            },
        )
        future = await api.post(
            "/v1/supplements/intakes",
            json={
                "intake_id": str(uuid4()),
                "product_id": product.id,
                "units": 1,
                "taken_at": (
                    datetime.now(ZoneInfo(ZONE)) + timedelta(hours=2)
                ).isoformat(),
            },
        )

    assert logged.status_code == 201, logged.text
    assert future.status_code == 400
    async with AsyncUnitOfWork() as uow:
        assert await uow.supplements.log_days(alice) == [(yesterday.date(), False)]


@pytest.mark.asyncio
async def test_adding_with_a_dose_needs_an_id_and_replays_on_retry(factory, pg_session):
    alice = await _user(pg_session)
    product = await _product("Omega 3")
    body = {"product_id": product.id, "log_now": True, "intake_id": str(uuid4())}
    purged: list = []

    async with _client(alice, purged=purged) as api:
        no_id = await api.post(
            "/v1/supplements/mine", json={"product_id": product.id, "log_now": True}
        )
        first = await api.post(
            "/v1/supplements/mine", json=body, headers={"Idempotency-Key": "add-1"}
        )
        retry = await api.post(
            "/v1/supplements/mine", json=body, headers={"Idempotency-Key": "add-1"}
        )
        mine = (await api.get("/v1/supplements/mine")).json()

    assert no_id.status_code == 422
    assert first.status_code == 201 and retry.json() == first.json()
    assert len(mine["items"][0]["doses"]) == 1
    today = datetime.now(ZoneInfo(ZONE)).date()
    assert purged == [(alice, today)]


@pytest.mark.asyncio
async def test_list_rows_update_and_removal_keep_the_days_doses(factory, pg_session):
    alice = await _user(pg_session)
    product = await _product("Zinc 25mg", barcodes=(BARCODE,))

    async with _client(alice) as api:
        found = await api.get(f"/v1/supplements/barcode/0{BARCODE}")
        added = (
            await api.post(
                "/v1/supplements/mine",
                json={
                    "product_id": product.id,
                    "log_now": True,
                    "intake_id": str(uuid4()),
                },
            )
        ).json()
        entry_id = added["entry"]["id"]
        patched = await api.patch(
            f"/v1/supplements/mine/{entry_id}",
            json={"units_per_dose": 2, "active": False},
        )
        removed = await api.delete(f"/v1/supplements/mine/{entry_id}")
        mine = (await api.get("/v1/supplements/mine")).json()

    assert found.status_code == 200 and found.json()["id"] == product.id
    assert patched.status_code == 200
    assert patched.json()["units_per_dose"] == 2 and patched.json()["active"] is False
    assert removed.status_code == 200
    assert mine["items"] == []
    [dose] = mine["unlisted_doses"]
    assert (
        dose["id"] == added["intake"]["id"] and dose["product"]["name"] == "Zinc 25mg"
    )


@pytest.mark.asyncio
async def test_food_like_products_and_naive_times_are_refused(factory, pg_session):
    alice = await _user(pg_session)
    shake = await _product("Protein shake", macro_bearing=True)
    pill = await _product("Vitamin C 500")

    async with _client(alice) as api:
        as_dose = await api.post(
            "/v1/supplements/intakes",
            json={"intake_id": str(uuid4()), "product_id": shake.id, "units": 1},
        )
        naive = await api.post(
            "/v1/supplements/intakes",
            json={
                "intake_id": str(uuid4()),
                "product_id": pill.id,
                "units": 1,
                "taken_at": "2026-11-01T08:00:00",
            },
        )

    assert as_dose.status_code == 400
    assert as_dose.json()["detail"]["error_code"] == "SUPPLEMENT_IS_FOOD"
    assert naive.status_code == 422


@pytest.mark.asyncio
async def test_a_one_letter_word_matches_only_at_a_word_start(factory, pg_session):
    alice = await _user(pg_session)
    k2 = await _product("Vitamin K2 MK-7", brand="Nature Made")
    await _product("Vitamin C 1000", brand="Blackmores", sold_in_vn=True)

    async with _client(alice) as api:
        found = (
            await api.get("/v1/supplements/search", params={"q": "vitamin k"})
        ).json()

    assert [p["id"] for p in found["items"]] == [k2.id]
