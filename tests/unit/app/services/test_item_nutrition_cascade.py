"""Scan items take macros from the shared parse-text cascade when grounding is on."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.services.food_reference_validation_service import (
    FoodReferenceValidationService,
)
from src.app.services.item_nutrition_cascade import (
    ground_scan_meal,
    local_catalog_lookup_keys,
)
from src.domain.model.meal import Meal, MealImage, MealStatus
from src.domain.model.nutrition import FoodItem, Macros, Nutrition
from src.domain.services.meal_suggestion.ingredient_name_normalizer import (
    normalize_food_name,
)
from src.domain.services.nutrition_resolver import normalize_food_lookup_name


def _meal(name: str = "White Rice", quantity: float = 150) -> Meal:
    timestamp = datetime(2026, 10, 29, 12, 0)
    return Meal(
        meal_id="00000000-0000-0000-0000-000000000301",
        user_id="00000000-0000-0000-0000-000000000302",
        status=MealStatus.READY,
        created_at=timestamp,
        ready_at=timestamp,
        image=MealImage(
            image_id="00000000-0000-0000-0000-000000000303",
            format="jpeg",
            size_bytes=12,
            url="https://example.com/image.jpg",
        ),
        source="scanner",
        dish_name="Rice",
        nutrition=Nutrition(
            macros=Macros(protein=4, carbs=45, fat=1),
            food_items=[
                FoodItem(
                    id="food-1",
                    name=name,
                    quantity=quantity,
                    unit="g",
                    macros=Macros(protein=4, carbs=45, fat=1),
                )
            ],
        ),
    )


def _local_rice() -> dict:
    return {
        "id": 42,
        "name": "White Rice",
        "is_verified": True,
        "source": "usda",
        "protein_100g": 2.7,
        "carbs_100g": 28.0,
        "fat_100g": 0.3,
        "fiber_100g": 0.4,
        "sugar_100g": 0.1,
    }


@pytest.mark.asyncio
async def test_local_reference_scales_scan_macros_by_grams():
    key = normalize_food_lookup_name("White Rice")
    batch_lookup = AsyncMock(return_value={key: _local_rice()})
    provider = MagicMock()
    provider.search_food_candidates = AsyncMock()
    meal = _meal()

    report = await ground_scan_meal(
        meal,
        batch_lookup=batch_lookup,
        provider=provider,
        timeout_seconds=1.0,
    )

    item = meal.nutrition.food_items[0]
    assert report.outcome == "match"
    assert report.items[0].source == "local"
    assert report.items[0].reason_code == "food_reference"
    assert item.macros.protein == pytest.approx(4.05)
    assert item.macros.carbs == pytest.approx(42.0)
    assert item.macros.fat == pytest.approx(0.45)
    assert item.macros.fiber == pytest.approx(0.6)
    assert item.macros.sugar == pytest.approx(0.15)
    assert item.food_reference_id == 42
    assert item.source_kind == "local"
    assert meal.nutrition.macros.protein == pytest.approx(4.05)
    assert meal.nutrition.calories == pytest.approx(
        Macros(protein=4.05, carbs=42.0, fat=0.45, fiber=0.6, sugar=0.15).total_calories
    )
    provider.search_food_candidates.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_timeout_keeps_vision_macros_as_ai_estimate():
    provider = MagicMock()
    provider.search_food_candidates = AsyncMock(side_effect=TimeoutError)
    provider.get_food_details = AsyncMock()
    meal = _meal()
    original = (
        meal.nutrition.food_items[0].macros.protein,
        meal.nutrition.food_items[0].macros.carbs,
        meal.nutrition.food_items[0].macros.fat,
    )

    report = await ground_scan_meal(
        meal,
        batch_lookup=AsyncMock(return_value={}),
        provider=provider,
        timeout_seconds=0.05,
    )

    item = meal.nutrition.food_items[0]
    assert report.outcome == "unresolved"
    assert report.items[0].reason_code == "ai_estimate"
    assert item.source_kind == "ai_estimate"
    assert (
        item.macros.protein,
        item.macros.carbs,
        item.macros.fat,
    ) == original
    assert meal.nutrition.macros.protein == 4
    provider.get_food_details.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_timeout_reports_timeout_and_keeps_ai_estimate():
    async def slow_lookup(_names):
        await asyncio.sleep(0.2)
        return {}

    meal = _meal()
    report = await ground_scan_meal(
        meal,
        batch_lookup=slow_lookup,
        provider=None,
        timeout_seconds=0.01,
    )

    assert report.outcome == "timeout"
    assert report.reason_code == "timeout"
    assert report.error_type == "TimeoutError"
    assert meal.nutrition.food_items[0].macros.protein == 4
    assert meal.nutrition.food_items[0].source_kind == "ai_estimate"
    assert report.items[0].reason_code == "ai_estimate"


@pytest.mark.asyncio
async def test_fatsecret_hit_scales_when_local_reference_misses():
    provider = MagicMock()
    provider.search_food_candidates = AsyncMock(
        return_value=[
            {
                "food_id": "fs-1",
                "food_name": "White Rice",
                "source": "fatsecret",
            }
        ]
    )
    provider.get_food_details = AsyncMock(
        return_value={
            "source": "fatsecret",
            "protein_100g": 2.7,
            "carbs_100g": 28.0,
            "fat_100g": 0.3,
            "fiber_100g": 0.4,
            "sugar_100g": 0.1,
            "calories_100g": 125,
            "metric_serving_amount": 100.0,
        }
    )
    meal = _meal()

    report = await ground_scan_meal(
        meal,
        batch_lookup=AsyncMock(return_value={}),
        provider=provider,
        timeout_seconds=1.0,
    )

    item = meal.nutrition.food_items[0]
    assert report.outcome == "match"
    assert report.items[0].source == "fatsecret"
    assert item.source_kind == "provider"
    assert item.macros.protein == pytest.approx(4.05)
    assert item.macros.carbs == pytest.approx(42.0)
    provider.get_food_details.assert_awaited_once_with("fs-1")


@pytest.mark.asyncio
async def test_validation_service_ground_meal_uses_batch_lookup():
    key = normalize_food_lookup_name("White Rice")
    batch_lookup = AsyncMock(return_value={key: _local_rice()})
    service = FoodReferenceValidationService(
        food_reference_batch_lookup=batch_lookup,
        timeout_seconds=1.0,
    )
    meal = _meal()

    report = await service.ground_meal(meal)

    assert report.outcome == "match"
    assert meal.nutrition.food_items[0].macros.protein == pytest.approx(4.05)
    batch_lookup.assert_awaited()


@pytest.mark.asyncio
async def test_validate_meal_does_not_replace_macros():
    """The old closeness audit stays compatible when grounding is off."""
    key = normalize_food_lookup_name("White Rice")
    service = FoodReferenceValidationService(
        food_reference_batch_lookup=AsyncMock(return_value={key: _local_rice()}),
        timeout_seconds=1.0,
    )
    meal = _meal()

    report = await service.validate_meal(meal)

    assert report.meal is meal
    assert meal.nutrition.food_items[0].macros.protein == 4
    assert meal.nutrition.food_items[0].source_kind is None


def test_local_catalog_keys_collapse_when_normalizers_agree():
    assert normalize_food_lookup_name("White Rice") == "white rice"
    assert normalize_food_name("White Rice") == "white rice"
    assert local_catalog_lookup_keys("White Rice") == ("white rice",)


def test_local_catalog_keys_try_lookup_then_seed_when_they_differ():
    assert normalize_food_lookup_name("Grilled Chicken") == "grilled chicken"
    assert normalize_food_name("Grilled Chicken") == "chicken"
    assert local_catalog_lookup_keys("Grilled Chicken") == (
        "grilled chicken",
        "chicken",
    )


def _verified_reference(**overrides) -> dict:
    row = {
        "id": 7,
        "name": "Chicken",
        "is_verified": True,
        "source": "usda",
        "protein_100g": 20.0,
        "carbs_100g": 0.0,
        "fat_100g": 5.0,
        "fiber_100g": 0.0,
        "sugar_100g": 0.0,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_grounding_hits_seed_key_when_lookup_key_misses():
    """normalize_food_lookup_name misses; the seed name_normalized still hits."""
    seed = _verified_reference()

    async def batch_lookup(names):
        assert names == ["chicken", "grilled chicken"]
        return {"chicken": seed}

    provider = MagicMock()
    provider.search_food_candidates = AsyncMock()
    meal = _meal(name="Grilled Chicken", quantity=100)

    report = await ground_scan_meal(
        meal,
        batch_lookup=batch_lookup,
        provider=provider,
        timeout_seconds=1.0,
    )

    item = meal.nutrition.food_items[0]
    assert report.outcome == "match"
    assert report.items[0].source == "local"
    assert item.food_reference_id == 7
    assert item.source_kind == "local"
    assert item.macros.protein == pytest.approx(20.0)
    assert item.macros.fat == pytest.approx(5.0)
    provider.search_food_candidates.assert_not_awaited()


@pytest.mark.asyncio
async def test_grounding_queries_an_identical_key_once():
    batch_lookup = AsyncMock(
        return_value={normalize_food_lookup_name("White Rice"): _local_rice()}
    )
    meal = _meal()

    report = await ground_scan_meal(
        meal,
        batch_lookup=batch_lookup,
        provider=MagicMock(),
        timeout_seconds=1.0,
    )

    batch_lookup.assert_awaited_once_with(["white rice"])
    assert report.items[0].source == "local"
    assert meal.nutrition.food_items[0].macros.protein == pytest.approx(4.05)


@pytest.mark.asyncio
async def test_grounding_prefers_existing_lookup_key_when_both_rows_exist():
    lookup_row = _verified_reference(id=1, name="Grilled Chicken", protein_100g=31.0)
    seed_row = _verified_reference(id=2, name="Chicken", protein_100g=20.0)

    async def batch_lookup(names):
        assert names == ["chicken", "grilled chicken"]
        return {"grilled chicken": lookup_row, "chicken": seed_row}

    meal = _meal(name="Grilled Chicken", quantity=100)
    await ground_scan_meal(
        meal,
        batch_lookup=batch_lookup,
        provider=MagicMock(),
        timeout_seconds=1.0,
    )

    item = meal.nutrition.food_items[0]
    assert item.food_reference_id == 1
    assert item.macros.protein == pytest.approx(31.0)
