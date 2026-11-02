"""POST /v1/supplements/scan-label on real PostgreSQL, with recorded model reads."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from src.api.base_dependencies import get_supplement_label_scan_quota
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exception_handlers import register_exception_handlers
from src.api.routes.v1.supplements import require_supplements
from src.api.routes.v1.supplements import router as supplements_router
from src.app.commands.meal.create_manual_meal_command import CreateManualMealCommand
from src.app.commands.supplement import ScanSupplementLabelCommand
from src.app.handlers.command_handlers.supplement_scan_handler import (
    ScanSupplementLabelCommandHandler,
)
from src.app.services.daily_quota import DailyQuota
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.event_bus.pymediator_event_bus import PyMediatorEventBus

pytestmark = pytest.mark.integration

IMAGE_ID = "8c1f2a9e-5b7d-4c3e-9f10-2a6b8d4e1c7a"
IMAGE_URL = f"https://imagedelivery.net/acct/{IMAGE_ID}/public"
ZINC = {
    "is_supplement_label": True,
    "brand": "Kirkland",
    "product_name": "Zinc 50 mg",
    "form": "tablet",
    "serving_units": 1,
    "rows": [
        {
            "name": "Zinc (as zinc gluconate)",
            "amount": 50,
            "unit": "mg",
            "nutrient": "zinc",
        }
    ],
}
WHEY = {
    "is_supplement_label": True,
    "product_kind": "protein_powder",
    "brand": "Optimum",
    "product_name": "Gold Standard Whey",
    "form": "powder",
    "serving_grams": 31,
    "rows": [{"name": "Calcium", "amount": 130, "unit": "mg", "nutrient": "calcium"}],
    "macros_per_serving": {
        "calories_kcal": 120,
        "protein_g": 24,
        "carbs_g": 3,
        "fat_g": 1.5,
    },
}


@pytest_asyncio.fixture
async def factory(monkeypatch, pg_session, async_session_factory):
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    monkeypatch.setattr(
        "src.api.routes.v1.supplements.require_subscription", _subscribed
    )
    await pg_session.execute(text("TRUNCATE TABLE supplement_products CASCADE"))
    await pg_session.commit()
    yield async_session_factory
    await pg_session.execute(text("TRUNCATE TABLE supplement_products CASCADE"))
    await pg_session.commit()


async def _subscribed(user_id: str) -> None:
    return None


async def _user(session) -> str:
    user = User(
        id=str(uuid4()),
        firebase_uid=f"uid-{uuid4()}",
        email=f"{uuid4()}@example.com",
        username=f"user-{uuid4()}",
        password_hash="unused",
        timezone="Asia/Ho_Chi_Minh",
    )
    session.add(user)
    await session.commit()
    return user.id


class _Vision:
    def __init__(self, read: dict):
        self.read, self.calls = read, 0

    async def analyze_with_strategy(self, image_bytes, strategy):
        self.calls += 1
        return {"structured_data": self.read}


class _Meals:
    def __init__(self):
        self.commands: list[CreateManualMealCommand] = []

    async def handle(self, command: CreateManualMealCommand) -> SimpleNamespace:
        # The real handler returns a Meal, not a dict.
        self.commands.append(command)
        macros = SimpleNamespace(protein=24.0, carbs=3.0, fat=1.5)
        return SimpleNamespace(
            meal_id=command.meal_id,
            dish_name=command.dish_name,
            user_id=command.user_id,
            nutrition=SimpleNamespace(calories=121.5, macros=macros),
        )


def _client(user_id: str, vision: _Vision, *, daily_limit: int = 20, meals=None):
    counts: dict[str, int] = {}

    async def count(key: str, ttl: int) -> int:
        counts[key] = counts.get(key, 0) + 1
        return counts[key]

    async def download(url: str) -> bytes:
        return b"jpeg"

    bus = PyMediatorEventBus()
    bus.register_handler(
        ScanSupplementLabelCommand,
        ScanSupplementLabelCommandHandler(
            uow_factory=AsyncUnitOfWork,
            vision_service=vision,
            download_image_bytes=download,
            event_bus=bus,
        ),
    )
    bus.register_handler(CreateManualMealCommand, meals or _Meals())
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(supplements_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    app.dependency_overrides[require_supplements] = lambda: None
    app.dependency_overrides[get_supplement_label_scan_quota] = lambda: DailyQuota(
        "supplement_label_scan",
        daily_limit,
        count,
        "SUPPLEMENT_LABEL_SCAN_DAILY_LIMIT",
        "limit",
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _body() -> dict:
    return {"image_url": IMAGE_URL, "image_id": IMAGE_ID, "intake_id": str(uuid4())}


@pytest.mark.asyncio
async def test_a_scan_saves_a_private_product_lists_it_and_logs_a_dose_once(
    factory, pg_session
):
    alice = await _user(pg_session)
    vision = _Vision(ZINC)
    body = _body()

    async with _client(alice, vision) as api:
        first = await api.post(
            "/v1/supplements/scan-label", json=body, headers={"Idempotency-Key": "s-1"}
        )
        retry = await api.post(
            "/v1/supplements/scan-label", json=body, headers={"Idempotency-Key": "s-1"}
        )

    assert first.status_code == 201, first.text
    assert retry.json() == first.json() and vision.calls == 1
    reply = first.json()
    assert reply["route"] == "supplement"
    assert reply["product"]["mine"] is True
    assert reply["product"]["review_status"] == "pending_review"
    assert reply["intake"]["nutrients"] == {"zinc": 50.0}
    async with AsyncUnitOfWork() as uow:
        assert len(await uow.supplements.list_supplements(alice)) == 1


@pytest.mark.asyncio
async def test_a_protein_powder_is_saved_as_a_meal_without_a_second_model_call(
    factory, pg_session
):
    alice = await _user(pg_session)
    vision, meals = _Vision(WHEY), _Meals()

    body = _body()
    async with _client(alice, vision, meals=meals) as api:
        response = await api.post(
            "/v1/supplements/scan-label", json=body, headers={"Idempotency-Key": "w"}
        )
        replay = await api.post(
            "/v1/supplements/scan-label", json=body, headers={"Idempotency-Key": "w"}
        )

    assert response.status_code == 201, response.text
    assert response.json()["route"] == "food" and vision.calls == 1
    assert replay.json() == response.json()
    assert response.json()["meal"]["meal_id"] == body["intake_id"]
    [command] = meals.commands
    [item] = command.items
    assert item.quantity == 31 and command.source == "food_label"
    assert round(item.custom_nutrition.protein_per_100g, 2) == round(24 * 100 / 31, 2)
    async with AsyncUnitOfWork() as uow:
        assert await uow.supplements.list_supplements(alice) == []


@pytest.mark.asyncio
async def test_unreadable_photos_quota_and_subscription_are_refused(
    factory, pg_session, monkeypatch
):
    alice = await _user(pg_session)

    async with _client(
        alice, _Vision({"is_supplement_label": False, "product_name": "x"})
    ) as api:
        blurry = await api.post(
            "/v1/supplements/scan-label", json=_body(), headers={"Idempotency-Key": "b"}
        )
        no_key = await api.post("/v1/supplements/scan-label", json=_body())
    async with _client(alice, _Vision(ZINC), daily_limit=1) as api:
        statuses = [
            (
                await api.post(
                    "/v1/supplements/scan-label",
                    json=_body(),
                    headers={"Idempotency-Key": f"q-{n}"},
                )
            ).status_code
            for n in range(2)
        ]

    async def unsubscribed(user_id: str) -> None:
        raise HTTPException(
            status_code=402, detail={"error_code": "SUBSCRIPTION_REQUIRED"}
        )

    monkeypatch.setattr(
        "src.api.routes.v1.supplements.require_subscription", unsubscribed
    )
    async with _client(alice, _Vision(ZINC)) as api:
        lapsed = await api.post(
            "/v1/supplements/scan-label", json=_body(), headers={"Idempotency-Key": "l"}
        )

    assert blurry.status_code == 400
    assert blurry.json()["detail"]["error_code"] == "SUPPLEMENT_LABEL_UNREADABLE"
    assert no_key.status_code == 400
    assert statuses == [201, 429]
    assert lapsed.status_code == 402


@pytest.mark.asyncio
async def test_a_rescan_with_a_known_barcode_reuses_the_product(factory, pg_session):
    alice = await _user(pg_session)
    with_barcode = {**ZINC, "barcode": "0096619756803"}

    async with _client(alice, _Vision(with_barcode)) as api:
        first = (
            await api.post(
                "/v1/supplements/scan-label",
                json=_body(),
                headers={"Idempotency-Key": "r1"},
            )
        ).json()
        second = (
            await api.post(
                "/v1/supplements/scan-label",
                json=_body(),
                headers={"Idempotency-Key": "r2"},
            )
        ).json()

    assert second["product"]["id"] == first["product"]["id"]
    assert len(second["entry"]["doses"]) == 2
