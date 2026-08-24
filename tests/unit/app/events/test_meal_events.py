"""Unit tests for meal integration events and publish_meal_event helper."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.app.events.meal.meal_events import (
    MealInsightSnapshot,
    publish_meal_event,
)
from src.domain.model.meal.meal import Meal, MealImage, MealStatus
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.nutrition import FoodItem, Nutrition


@pytest.fixture
def sample_meal():
    user_id = str(uuid4())
    meal_id = str(uuid4())
    image_id = str(uuid4())
    now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
    nutrition = Nutrition(
        macros=Macros(protein=30.0, carbs=50.0, fat=10.0, fiber=5.0, sugar=2.0),
        confidence_score=0.95,
        food_items=[
            FoodItem(
                id="1",
                name="Chicken Breast",
                quantity=150.0,
                unit="g",
                macros=Macros(protein=30.0, carbs=0.0, fat=5.0, fiber=0.0, sugar=0.0),
                confidence=0.9,
            )
        ],
    )
    return Meal(
        meal_id=meal_id,
        user_id=user_id,
        dish_name="Chicken Dish",
        status=MealStatus.READY,
        nutrition=nutrition,
        image=MealImage(image_id=image_id, url="https://example.com/img.jpg", format="jpeg", size_bytes=1024),
        created_at=now,
        ready_at=now,
    )


def test_meal_insight_snapshot_from_meal(sample_meal):
    snapshot = MealInsightSnapshot.from_meal(
        sample_meal,
        language="vi-VN",
        user_context={"goal": "gain_muscle"},
        tokens=["fcm_token_123"],
    )

    assert snapshot.dish_name == "Chicken Dish"
    assert snapshot.language == "vi"
    assert snapshot.nutrition.calories == 400.0
    assert snapshot.nutrition.protein_g == 30.0
    assert len(snapshot.ingredients) == 1
    assert snapshot.ingredients[0].name == "Chicken Breast"
    assert snapshot.ingredients[0].quantity == 150.0
    assert snapshot.user_context == {"goal": "gain_muscle"}
    assert snapshot.tokens == ["fcm_token_123"]


@pytest.mark.asyncio
async def test_publish_meal_event_success(sample_meal):
    publisher = AsyncMock()

    published = await publish_meal_event(
        publisher,
        sample_meal,
        event_type="created",
        environment="production",
        meal_date=date(2026, 8, 24),
        language="en",
        old_meal_date=date(2026, 8, 23),
    )

    assert published is True
    publisher.publish.assert_awaited_once()
    payload = publisher.publish.call_args[0][0]
    assert payload["event_type"] == "meal.created.v1"
    assert payload["aggregate_id"] == sample_meal.meal_id
    assert payload["data"]["user_id"] == sample_meal.user_id
    assert payload["data"]["meal_date"] == "2026-08-24"
    assert payload["data"]["language"] == "en"
    assert payload["data"]["old_meal_date"] == "2026-08-23"


@pytest.mark.asyncio
async def test_publish_meal_event_none_publisher_returns_false(sample_meal):
    published = await publish_meal_event(
        None,
        sample_meal,
        event_type="created",
        environment="production",
        meal_date=date(2026, 8, 24),
    )
    assert published is False
