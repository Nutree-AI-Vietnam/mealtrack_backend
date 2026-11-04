"""POST and DELETE /v1/log/describe on real PostgreSQL, with recorded model replies.

The real meal, water, activity and supplement writers run inside one unit of
work, so these tests show what the database keeps: everything or nothing on
save, everything gone on undo.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text

from src.api.base_dependencies import (
    get_async_food_reference_repository,
    get_describe_log_guard,
)
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exception_handlers import register_exception_handlers
from src.api.routes.v1.log_describe import router
from src.app.commands.describe_any import DescribeAnyCommand, UndoDescribeAnyCommand
from src.app.commands.meal.parse_meal_text_command import ParseMealTextCommand
from src.app.handlers.command_handlers.create_manual_meal_command_handler import (
    CreateManualMealCommandHandler,
)
from src.app.handlers.command_handlers.describe_any_command_handler import (
    DescribeAnyCommandHandler,
)
from src.app.handlers.command_handlers.supplement_command_handlers import (
    SupplementWrite,
)
from src.app.handlers.command_handlers.undo_describe_any_command_handler import (
    UndoDescribeAnyCommandHandler,
)
from src.app.schemas.meal_schemas import ParsedFoodItemDto, ParseMealTextResponseDto
from src.app.services.describe_log_guard import DescribeLogGuard
from src.domain.model.meal import MealStatus
from src.domain.model.supplement import (
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementNutrients,
    SupplementProduct,
    UserSupplement,
)
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.event_bus.pymediator_event_bus import PyMediatorEventBus

pytestmark = pytest.mark.integration

ZONE = "Asia/Ho_Chi_Minh"
URL = "/v1/log/describe"
GRAMS = [{"unit": "g", "gram_weight": 1.0, "description": "1 g"}]


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


async def _product(name: str, owner: str | None = None, *, listed: bool = False):
    product = SupplementProduct(
        id=str(uuid4()),
        name=name,
        brand="Blackmores",
        form=SupplementForm.SOFTGEL,
        unit_label="viên nang mềm",
        source=ProductSource.CURATED if owner is None else ProductSource.LABEL_SCAN,
        review_status=ReviewStatus.APPROVED if owner is None else ReviewStatus.PRIVATE,
        owner_user_id=owner,
        nutrients=SupplementNutrients({"vitamin_d": 25.0}),
    )
    async with AsyncUnitOfWork() as uow:
        await uow.supplements.add_product(product)
        if listed and owner is not None:
            await uow.supplements.save_list_entry(
                UserSupplement(str(uuid4()), owner, product.id, units_per_dose=1)
            )
    return product


def _sentence(**parts: Any) -> dict[str, Any]:
    return {
        "food": parts.get("food"),
        "drinks": parts.get("drinks", []),
        "activities": parts.get("activities", []),
        "supplements": parts.get("supplements", []),
    }


def _supplement(name: str, **fields: Any) -> dict[str, Any]:
    return {"name": name, "words": f"1 viên {name}", "units": 1, **fields}


FOUR_KINDS = _sentence(
    food="phở bò",
    drinks=[{"catalog_id": "water", "name": "nước", "servings": 1}],
    activities=[{"catalog_id": "running", "name": "chạy", "duration_min": 30}],
    supplements=[_supplement("D3", list_ref="s1")],
)


class _Model:
    def __init__(self, reply: dict[str, Any]):
        self.reply, self.calls = reply, []

    async def generate_meal_plan_async(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return copy.deepcopy(self.reply)


class _FoodParser:
    """Stands in for the parse-text pipeline: 450 g of phở at 88.5 kcal/100 g."""

    def __init__(self):
        self.texts: list[str] = []

    async def handle(self, command: ParseMealTextCommand) -> ParseMealTextResponseDto:
        self.texts.append(command.text)
        density = {
            "protein_per_100g": 6.0,
            "carbs_per_100g": 10.0,
            "fat_per_100g": 2.5,
            "fiber_per_100g": 0.0,
            "sugar_per_100g": 0.0,
            "calories_per_100g": 86.5,
        }
        item = ParsedFoodItemDto(
            name="Phở bò",
            quantity=450,
            unit="g",
            protein=27.0,
            carbs=45.0,
            fat=11.25,
            allowed_units=GRAMS,
            origin="custom",
            nutrition_contract_version="2",
            source_snapshot={"basis": "100g", "origin": "custom", **density},
            **density,
        )
        return ParseMealTextResponseDto(
            items=[item],
            total_protein=27.0,
            total_carbs=45.0,
            total_fat=11.25,
            emoji="🍜",
        )


class _Env:
    def __init__(self, user_id: str, reply: dict[str, Any], *, supplements_on=True):
        self.model = _Model(reply)
        self.food = _FoodParser()
        self.publisher = AsyncMock()
        self.purged: list = []
        bus = PyMediatorEventBus()

        async def purge(user_id: str, day) -> None:
            self.purged.append(day)

        async def available() -> bool:
            return supplements_on

        bus.register_handler(ParseMealTextCommand, self.food)
        bus.register_handler(
            DescribeAnyCommand,
            DescribeAnyCommandHandler(
                uow_factory=AsyncUnitOfWork,
                meal_generation_service=self.model,
                event_bus=bus,
                meal_writer=CreateManualMealCommandHandler(
                    uow_factory=AsyncUnitOfWork,
                    event_publisher=self.publisher,
                    event_bus=bus,
                ),
                supplement_writer=SupplementWrite(AsyncUnitOfWork, purge),
                supplements_available=available,
                event_publisher=self.publisher,
            ),
        )
        bus.register_handler(
            UndoDescribeAnyCommand,
            UndoDescribeAnyCommandHandler(
                uow_factory=AsyncUnitOfWork,
                event_publisher=self.publisher,
                purge_supplement_day=purge,
            ),
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(router)
        app.dependency_overrides[get_current_user_id] = lambda: user_id
        app.dependency_overrides[get_configured_event_bus] = lambda: bus
        app.dependency_overrides[get_async_food_reference_repository] = lambda: None
        app.dependency_overrides[get_describe_log_guard] = lambda: DescribeLogGuard(
            enabled=True,
            daily_limit=60,
            require_subscription=AsyncMock(return_value=None),
            increment_counter=AsyncMock(return_value=1),
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )

    async def post(self, request_id: str, **body: Any) -> httpx.Response:
        return await self.client.post(
            URL, json={"text": "phở, nước, chạy, D3", "request_id": request_id, **body}
        )


def _today():
    return datetime.now(ZoneInfo(ZONE)).date()


async def _rows(session, user_id: str) -> int:
    """Meals, drinks and activities the user has, of any status."""
    result = await session.execute(
        text(
            "SELECT (SELECT count(*) FROM meal WHERE user_id = :u)"
            " + (SELECT count(*) FROM hydration_entries WHERE user_id = :u)"
            " + (SELECT count(*) FROM movement_entries WHERE user_id = :u)"
        ),
        {"u": user_id},
    )
    return int(result.scalar_one())


async def _doses(user_id: str) -> list:
    async with AsyncUnitOfWork() as uow:
        return await uow.supplements.intakes_between(user_id, _today(), _today())


@pytest.mark.asyncio
async def test_one_sentence_saves_four_kinds_replays_and_undoes_in_one_go(
    factory, pg_session
):
    alice = await _user(pg_session)
    d3 = await _product("Vitamin D3 1000 IU", alice, listed=True)
    env = _Env(alice, FOUR_KINDS)

    first = await env.post("r-1")
    replay = await env.post("r-1")

    assert first.status_code == 200, first.text
    assert replay.json() == first.json()
    assert len(env.model.calls) == 1 and env.food.texts == ["phở bò"]
    assert "s1: Blackmores Vitamin D3 1000 IU" in env.model.calls[0]["prompt"]
    assert env.model.calls[0]["model_purpose"] == "describe_log"
    saved = first.json()["saved"]
    assert [item["kind"] for item in saved] == [
        "food",
        "drink",
        "activity",
        "supplement",
    ]
    food, drink, activity, dose = saved
    assert food["meal"]["meal_id"] == food["id"] and food["summary"] == "389 kcal"
    assert drink["drink"]["meal_id"] is not None  # water keeps its legacy meal row
    assert activity["activity"]["duration_min"] == 30
    assert dose["intake"]["product_id"] == d3.id and dose["estimated"] is False
    assert dose["intake"]["nutrients"] == {"vitamin_d": 25.0}
    assert first.json()["nothing_found"] is False
    async with AsyncUnitOfWork() as uow:
        assert (await uow.meals.find_by_id(food["id"])).dish_name == "Phở bò"
        assert await uow.hydration_entries.find_by_id_or_legacy_meal_id(
            alice, drink["id"]
        )
        assert await uow.movement_entries.find_by_id(alice, activity["id"])
    assert [one.id for one in await _doses(alice)] == [dose["id"]]

    undo = await env.client.delete(f"{URL}/r-1")
    again = await env.client.delete(f"{URL}/r-1")

    assert undo.status_code == 200, undo.text
    assert undo.json()["deleted"] == {
        "food": 1,
        "drink": 1,
        "activity": 1,
        "supplement": 1,
    }
    assert set(again.json()["deleted"].values()) == {0}
    async with AsyncUnitOfWork() as uow:
        assert await uow.meals.find_by_id(food["id"]) is None
        assert (
            await uow.hydration_entries.find_by_id_or_legacy_meal_id(alice, drink["id"])
            is None
        )
        legacy = await uow.meals.find_by_id(drink["drink"]["meal_id"])
        assert legacy is None or legacy.status == MealStatus.INACTIVE
        assert await uow.movement_entries.find_by_id(alice, activity["id"]) is None
        # D3 was on the list before the sentence, so it stays there.
        assert [
            e.product_id for e in await uow.supplements.list_supplements(alice)
        ] == [d3.id]
    assert await _doses(alice) == []


@pytest.mark.asyncio
async def test_supplements_match_the_catalog_or_become_private_estimates(
    factory, pg_session
):
    alice, bob = await _user(pg_session), await _user(pg_session)
    bio_c = await _product("Bio C 1000")
    await _product("Bob Secret Zinc", bob)
    env = _Env(
        alice,
        _sentence(
            supplements=[
                _supplement("Bio C", catalog_query="Blackmores Bio C"),
                # Bob's private product must never match, by name or by ref.
                _supplement(
                    "Bob Secret Zinc",
                    catalog_query="Bob Secret Zinc",
                    estimate={
                        "form": "tablet",
                        "nutrients": [{"nutrient": "zinc", "amount": 15, "unit": "mg"}],
                    },
                ),
                _supplement("D3", list_ref="s7"),
            ]
        ),
    )

    response = await env.post("r-2")

    assert response.status_code == 200, response.text
    catalog, zinc, d3 = response.json()["saved"]
    assert catalog["intake"]["product_id"] == bio_c.id
    assert catalog["estimated"] is False
    assert zinc["estimated"] is True and zinc["product"]["mine"] is True
    assert zinc["product"]["review_status"] == "private"
    assert zinc["product"]["source"] == "describe"
    assert zinc["intake"]["nutrients"] == {"zinc": 15.0}
    assert d3["estimated"] is True and d3["intake"]["nutrients"] == {}
    async with AsyncUnitOfWork() as uow:
        assert len(await uow.supplements.list_supplements(alice)) == 3

    undo = await env.client.delete(f"{URL}/r-2")

    assert undo.json()["deleted"]["supplement"] == 3
    async with AsyncUnitOfWork() as uow:
        # The sentence added all three to the list, so undo takes them off.
        assert await uow.supplements.list_supplements(alice) == []


@pytest.mark.asyncio
async def test_whey_is_food_and_kinds_limit_what_is_saved(factory, pg_session):
    alice = await _user(pg_session)
    env = _Env(
        alice,
        _sentence(
            food="cơm tấm",
            supplements=[
                {
                    "name": "whey",
                    "words": "1 muỗng whey",
                    "product_kind": "protein_powder",
                },
                _supplement("D3"),
            ],
        ),
    )

    all_kinds = await env.post("r-3")
    supplement_only = await env.post("r-4", kinds=["supplement"])

    assert env.food.texts == ["cơm tấm, 1 muỗng whey"]
    assert [i["kind"] for i in all_kinds.json()["saved"]] == ["food", "supplement"]
    body = supplement_only.json()
    assert [i["kind"] for i in body["saved"]] == ["supplement"]
    assert {(s["kind"], s["reason"]) for s in body["skipped"]} == {
        ("food", "not_requested")
    }


@pytest.mark.asyncio
async def test_supplements_switched_off_are_skipped_while_food_saves(
    factory, pg_session
):
    alice = await _user(pg_session)
    env = _Env(alice, FOUR_KINDS, supplements_on=False)

    response = await env.post("r-5")

    body = response.json()
    assert [i["kind"] for i in body["saved"]] == ["food", "drink", "activity"]
    assert body["skipped"] == [
        {"kind": "supplement", "title": "D3", "reason": "supplements_unavailable"}
    ]
    assert "my_supplements" not in env.model.calls[0]["prompt"]
    assert await _doses(alice) == []


@pytest.mark.asyncio
async def test_a_failure_in_the_last_writer_saves_nothing_and_frees_the_id(
    factory, pg_session, monkeypatch
):
    alice = await _user(pg_session)
    await _product("Vitamin D3 1000 IU", alice, listed=True)
    env = _Env(alice, FOUR_KINDS)
    save_dose = SupplementWrite.save_dose
    monkeypatch.setattr(
        SupplementWrite, "save_dose", AsyncMock(side_effect=RuntimeError("boom"))
    )

    failed = await env.post("r-6")

    assert failed.status_code == 500
    assert await _rows(pg_session, alice) == 0
    assert (await env.client.delete(f"{URL}/r-6")).status_code == 404

    monkeypatch.setattr(SupplementWrite, "save_dose", save_dose)
    retried = await env.post("r-6")

    assert retried.status_code == 200, retried.text
    assert len(retried.json()["saved"]) == 4 and len(env.model.calls) == 2


@pytest.mark.asyncio
async def test_nothing_found_is_an_answer_not_an_error(factory, pg_session):
    alice = await _user(pg_session)
    env = _Env(alice, _sentence())

    response = await env.post("r-7", text="hôm nay trời đẹp")

    assert response.status_code == 200
    assert response.json() == {"saved": [], "skipped": [], "nothing_found": True}
    assert env.food.texts == []
