"""Tests for optional food reference validation."""

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.services.food_reference_validation_service import (
    FoodReferenceValidationService,
)
from src.domain.model.meal import Meal, MealImage, MealStatus
from src.domain.model.nutrition import FoodItem, Macros, Nutrition


def _meal(source: str = "scanner") -> Meal:
    timestamp = datetime(2026, 7, 7, 12, 0)
    return Meal(
        meal_id="00000000-0000-0000-0000-000000000201",
        user_id="00000000-0000-0000-0000-000000000202",
        status=MealStatus.READY,
        created_at=timestamp,
        ready_at=timestamp,
        image=MealImage(
            image_id="00000000-0000-0000-0000-000000000101",
            format="jpeg",
            size_bytes=12,
            url="https://example.com/image.jpg",
        ),
        source=source,
        dish_name="Rice",
        nutrition=Nutrition(
            macros=Macros(protein=4, carbs=45, fat=1),
            food_items=[
                FoodItem(
                    id="food-1",
                    name="White Rice",
                    quantity=150,
                    unit="g",
                    macros=Macros(protein=4, carbs=45, fat=1),
                    allowed_units=[
                        {"unit": "g", "gram_weight": 1.0, "description": "1 g"}
                    ],
                )
            ],
        ),
    )


@pytest.mark.asyncio
async def test_local_reference_match_avoids_provider():
    repository = MagicMock()
    repository.find_batch_by_normalized_names = AsyncMock(
        return_value={"white rice": {"name": "White Rice"}}
    )
    provider = MagicMock()
    provider.search_food_candidates = AsyncMock()
    service = FoodReferenceValidationService(
        food_reference_repository=repository,
        nutrition_reference_provider=provider,
    )
    meal = _meal()

    report = await service.validate_meal(meal)

    assert report.meal is meal
    repository.find_batch_by_normalized_names.assert_awaited_once_with(["white rice"])
    provider.search_food_candidates.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_local_reference_lookup_avoids_provider():
    batch_lookup = AsyncMock(return_value={"white rice": {"name": "White Rice"}})
    provider = MagicMock()
    provider.search_food_candidates = AsyncMock()
    service = FoodReferenceValidationService(
        food_reference_batch_lookup=batch_lookup,
        nutrition_reference_provider=provider,
    )
    meal = _meal()

    report = await service.validate_meal(meal)

    assert report.meal is meal
    batch_lookup.assert_awaited_once_with(["white rice"])
    provider.search_food_candidates.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_reference_lookup_callable_avoids_provider():
    lookup = AsyncMock(return_value={"name": "White Rice"})
    provider = MagicMock()
    provider.search_food_candidates = AsyncMock()
    service = FoodReferenceValidationService(
        food_reference_lookup=lookup,
        nutrition_reference_provider=provider,
    )
    meal = _meal()

    report = await service.validate_meal(meal)

    assert report.meal is meal
    lookup.assert_awaited_once_with("white rice")
    provider.search_food_candidates.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_details_only_selected_candidate():
    repository = MagicMock()
    repository.find_batch_by_normalized_names = AsyncMock(return_value={})
    repository.find_by_normalized_name = AsyncMock(return_value=None)
    provider = MagicMock()
    provider.search_food_candidates = AsyncMock(
        return_value=[
            {"food_id": "selected", "description": "White Rice"},
            {"food_id": "ignored", "description": "Brown Rice"},
        ]
    )
    provider.get_food_details = AsyncMock(
        return_value={
            "protein_100g": 2.7,
            "carbs_100g": 30,
            "fat_100g": 0.7,
            "extra_nutrients": {"iron_mg": 0.4, "sodium_mg": 2, "potassium_mg": 35},
            "allowed_units": [
                {"unit": "serving", "gram_weight": 150, "description": "1 bowl"}
            ],
        }
    )
    service = FoodReferenceValidationService(
        food_reference_repository=repository,
        nutrition_reference_provider=provider,
    )
    meal = _meal()

    report = await service.validate_meal(meal)

    assert report.meal is meal
    assert report.outcome == "match"
    assert report.error_type is None
    assert report.items[0].outcome == "match"
    assert report.items[0].source == "provider"
    assert report.items[0].reason_code == "matched"
    provider.search_food_candidates.assert_awaited_once_with(
        "White Rice", max_results=3
    )
    provider.get_food_details.assert_awaited_once_with("selected")
    assert meal.nutrition.food_items[0].allowed_units == [
        {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
        {"unit": "serving", "gram_weight": 150.0, "description": "1 bowl"},
    ]
    micros = meal.nutrition.food_items[0].micros
    assert micros is not None
    assert micros.iron == pytest.approx(0.6)
    assert micros.sodium == pytest.approx(3)
    assert meal.nutrition.food_items[0].source_snapshot["extra_nutrients"][
        "iron_mg"
    ] == (0.4)


@pytest.mark.asyncio
async def test_divergent_provider_details_do_not_enrich_item():
    provider = MagicMock()
    provider.search_food_candidates = AsyncMock(
        return_value=[{"food_id": "selected", "description": "White Rice"}]
    )
    provider.get_food_details = AsyncMock(
        return_value={
            "protein_100g": 90,
            "carbs_100g": 1,
            "fat_100g": 90,
            "allowed_units": [
                {"unit": "serving", "gram_weight": 150, "description": "wrong"}
            ],
        }
    )
    service = FoodReferenceValidationService(nutrition_reference_provider=provider)
    meal = _meal()
    original_units = meal.nutrition.food_items[0].allowed_units

    report = await service.validate_meal(meal)

    assert report.meal is meal
    assert meal.nutrition.food_items[0].allowed_units == original_units


@pytest.mark.asyncio
async def test_provider_timeout_returns_original_meal():
    provider = MagicMock()

    async def slow_search(*args, **kwargs):
        await asyncio.sleep(0.05)

    provider.search_food_candidates = AsyncMock(side_effect=slow_search)
    service = FoodReferenceValidationService(
        nutrition_reference_provider=provider,
        timeout_seconds=0.001,
    )
    meal = _meal()

    report = await service.validate_meal(meal)

    assert report.meal is meal
    assert report.outcome == "timeout"
    assert report.reason_code == "timeout"
    assert report.error_type == "TimeoutError"
    assert report.items == ()


@pytest.mark.asyncio
async def test_macro_divergence_is_reported_without_enriching():
    provider = MagicMock()
    provider.search_food_candidates = AsyncMock(
        return_value=[
            {"food_id": "selected", "description": "White Rice", "source": "fatsecret"}
        ]
    )
    provider.get_food_details = AsyncMock(
        return_value={
            "source": "fatsecret",
            "protein_100g": 20,
            "carbs_100g": 10,
            "fat_100g": 5,
        }
    )
    service = FoodReferenceValidationService(nutrition_reference_provider=provider)
    meal = _meal()
    original_units = meal.nutrition.food_items[0].allowed_units

    report = await service.validate_meal(meal)

    assert report.outcome == "diverge"
    assert report.meal is meal
    assert report.items[0].outcome == "diverge"
    assert report.items[0].source == "fatsecret"
    assert report.items[0].reason_code == "macro_divergence"
    assert meal.nutrition.food_items[0].allowed_units == original_units
    assert meal.nutrition.food_items[0].micros is None


@pytest.mark.asyncio
async def test_provider_exception_is_reported_without_its_message():
    provider = MagicMock()
    provider.search_food_candidates = AsyncMock(
        side_effect=RuntimeError("provider token secret-value")
    )
    service = FoodReferenceValidationService(nutrition_reference_provider=provider)
    meal = _meal()

    report = await service.validate_meal(meal)

    assert report.outcome == "error"
    assert report.error_type == "RuntimeError"
    assert report.reason_code == "provider_error"
    assert report.meal is meal
    dumped = json.dumps(report.to_audit_dict())
    assert "secret-value" not in dumped
    assert "provider token" not in dumped


@pytest.mark.asyncio
async def test_food_label_meal_skips_reference_validation():
    provider = MagicMock()
    provider.search_food_candidates = AsyncMock()
    service = FoodReferenceValidationService(nutrition_reference_provider=provider)
    meal = _meal(source="food_label")

    report = await service.validate_meal(meal)

    assert report.meal is meal
    assert report.outcome == "skipped"
    assert report.reason_code == "food_label"
    provider.search_food_candidates.assert_not_awaited()
