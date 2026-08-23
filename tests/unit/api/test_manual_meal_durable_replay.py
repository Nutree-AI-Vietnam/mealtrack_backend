"""Route-level durable replay behavior for POST /v1/meals/manual."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.v1.meals_manual_text import create_manual_meal
from src.api.schemas.request.meal_requests import (
    CreateManualMealFromFoodsRequest,
    ManualMealItemRequest,
)
from src.infra.services.durable_write_service import DurableWriteRecord


def _payload() -> CreateManualMealFromFoodsRequest:
    return CreateManualMealFromFoodsRequest(
        dish_name="Oats",
        meal_type="breakfast",
        target_date="2026-08-11",
        source="manual",
        items=[
            ManualMealItemRequest(
                fdc_id=123,
                name="Oats",
                quantity=40,
                unit="g",
            )
        ],
    )


def _prepared_v2_payload() -> CreateManualMealFromFoodsRequest:
    return CreateManualMealFromFoodsRequest(
        dish_name="Rice plate",
        source="prompt",
        nutrition_contract_version=2,
        items=[
            ManualMealItemRequest(
                name="Pork rib",
                quantity=1,
                unit="slice",
                origin="local",
                food_reference_id=42,
                serving_options=[
                    {"unit": "g", "gram_weight": 1, "description": "1 g"},
                    {"unit": "slice", "gram_weight": 30, "description": "1 slice"},
                ],
                custom_nutrition={
                    "protein_per_100g": 90,
                    "carbs_per_100g": 0,
                    "fat_per_100g": 50,
                },
                source_snapshot={
                    "basis": "100g",
                    "protein_per_100g": 90,
                    "carbs_per_100g": 0,
                    "fat_per_100g": 50,
                },
            )
        ],
    )


@pytest.mark.asyncio
async def test_manual_meal_replays_stored_response():
    stored = DurableWriteRecord(
        request_fingerprint="abc",
        response_status_code=200,
        response_body={
            "meal_id": "meal-1",
            "status": "success",
            "message": "Meal 'Oats' created successfully",
            "created_at": "2026-08-11T00:00:00+00:00",
            "meal_detail": None,
        },
        resource_id="meal-1",
    )
    with (
        patch(
            "src.api.routes.v1.meals_manual_text.begin_durable_write",
            new=AsyncMock(return_value=stored),
        ),
        patch(
            "src.api.routes.v1.meals_manual_text.manual_meal_fingerprint",
            return_value="abc",
        ),
    ):
        response = await create_manual_meal.__wrapped__(
            request=SimpleNamespace(),
            payload=_payload(),
            user_id="user-1",
            event_bus=AsyncMock(),
            cache_service=None,
            task_manager=None,
            ai_manager=AsyncMock(),
            idempotency_key_header="op-1",
        )
    assert response.meal_id == "meal-1"


@pytest.mark.asyncio
async def test_manual_meal_conflict_returns_409():
    from src.infra.services.durable_write_service import DurableWriteConflictError

    with (
        patch(
            "src.api.routes.v1.meals_manual_text.begin_durable_write",
            new=AsyncMock(side_effect=DurableWriteConflictError),
        ),
        patch(
            "src.api.routes.v1.meals_manual_text.manual_meal_fingerprint",
            return_value="abc",
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await create_manual_meal.__wrapped__(
                request=SimpleNamespace(),
                payload=_payload(),
                user_id="user-1",
                event_bus=AsyncMock(),
                cache_service=None,
                task_manager=None,
                ai_manager=AsyncMock(),
                idempotency_key_header="op-1",
            )
    assert exc.value.status_code == 409
    assert exc.value.detail["error_code"] == "IDEMPOTENCY_KEY_CONFLICT"


@pytest.mark.asyncio
async def test_manual_meal_in_progress_returns_409_without_create():
    from src.infra.services.durable_write_service import DurableWriteInProgressError

    event_bus = AsyncMock()
    with (
        patch(
            "src.api.routes.v1.meals_manual_text.begin_durable_write",
            new=AsyncMock(side_effect=DurableWriteInProgressError),
        ),
        patch(
            "src.api.routes.v1.meals_manual_text.manual_meal_fingerprint",
            return_value="abc",
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await create_manual_meal.__wrapped__(
                request=SimpleNamespace(),
                payload=_payload(),
                user_id="user-1",
                event_bus=event_bus,
                cache_service=None,
                task_manager=None,
                ai_manager=AsyncMock(),
                idempotency_key_header="op-1",
            )
    assert exc.value.status_code == 409
    assert exc.value.detail["error_code"] == "IDEMPOTENCY_KEY_IN_PROGRESS"
    event_bus.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_meal_persists_durable_write_on_first_create():
    meal = SimpleNamespace(
        meal_id="meal-new",
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    event_bus = AsyncMock()
    event_bus.send = AsyncMock(return_value=meal)
    complete = AsyncMock(
        return_value=DurableWriteRecord(
            request_fingerprint="fp-1",
            response_status_code=200,
            response_body={
                "meal_id": "meal-new",
                "status": "success",
                "message": "Meal 'Oats' created successfully",
                "created_at": "2026-08-11T00:00:00+00:00",
                "meal_detail": None,
            },
            resource_id="meal-new",
        )
    )
    with (
        patch(
            "src.api.routes.v1.meals_manual_text.begin_durable_write",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "src.api.routes.v1.meals_manual_text.manual_meal_fingerprint",
            return_value="fp-1",
        ),
        patch(
            "src.api.routes.v1.meals_manual_text.complete_durable_write",
            new=complete,
        ),
        patch(
            "src.api.routes.v1.meals_manual_text.schedule_value_insight_generation",
        ),
        patch(
            "src.api.routes.v1.meals_manual_text.MealMapper.to_detailed_response",
            return_value=None,
        ),
        patch(
            "src.api.routes.v1.meals_manual_text.get_request_language",
            return_value="en",
        ),
    ):
        response = await create_manual_meal.__wrapped__(
            request=SimpleNamespace(),
            payload=_payload(),
            user_id="user-1",
            event_bus=event_bus,
            cache_service=None,
            task_manager=None,
            ai_manager=AsyncMock(),
            idempotency_key_header="op-2",
        )
    assert response.meal_id == "meal-new"
    complete.assert_awaited_once()
    assert complete.await_args.kwargs["idempotency_key"] == "op-2"
    assert complete.await_args.kwargs["request_fingerprint"] == "fp-1"


@pytest.mark.asyncio
async def test_manual_meal_route_forwards_prepared_nutrition_contract():
    meal = SimpleNamespace(
        meal_id="meal-v2",
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    event_bus = AsyncMock()
    event_bus.send = AsyncMock(return_value=meal)
    with (
        patch(
            "src.api.routes.v1.meals_manual_text.schedule_value_insight_generation"
        ),
        patch(
            "src.api.routes.v1.meals_manual_text.MealMapper.to_detailed_response",
            return_value=None,
        ),
        patch(
            "src.api.routes.v1.meals_manual_text.get_request_language",
            return_value="en",
        ),
    ):
        response = await create_manual_meal.__wrapped__(
            request=SimpleNamespace(),
            payload=_prepared_v2_payload(),
            user_id="user-1",
            event_bus=event_bus,
            cache_service=None,
            task_manager=None,
            ai_manager=AsyncMock(),
            x_nutrition_contract_version=2,
            x_app_version="1.0.0",
            x_platform="ios",
            idempotency_key_header="op-v2",
        )

    command = event_bus.send.await_args.args[0]
    item = command.items[0]
    assert response.meal_id == "meal-v2"
    assert item.nutrition_contract_version == "2"
    assert item.source_kind == "local"
    assert item.source_snapshot["basis"] == "100g"
    assert "serving_options" not in item.source_snapshot


@pytest.mark.asyncio
async def test_manual_meal_abandons_claim_when_create_fails():
    event_bus = AsyncMock()
    event_bus.send = AsyncMock(side_effect=RuntimeError("db down"))
    abandon = AsyncMock()
    with (
        patch(
            "src.api.routes.v1.meals_manual_text.begin_durable_write",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "src.api.routes.v1.meals_manual_text.manual_meal_fingerprint",
            return_value="fp-1",
        ),
        patch(
            "src.api.routes.v1.meals_manual_text.abandon_durable_write",
            new=abandon,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await create_manual_meal.__wrapped__(
                request=SimpleNamespace(),
                payload=_payload(),
                user_id="user-1",
                event_bus=event_bus,
                cache_service=None,
                task_manager=None,
                ai_manager=AsyncMock(),
                idempotency_key_header="op-3",
            )
    assert exc.value.status_code == 500
    abandon.assert_awaited_once()
