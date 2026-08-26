"""
Unit tests for food database feature: search, details, and manual meal creation.
"""

from typing import Any

import pytest


# Stub service and cache for tests
class StubFoodDataService:
    async def search_foods(
        self, query: str, limit: int = 20, max_results: int = 20
    ) -> list[dict[str, Any]]:
        return [
            {
                "description": "Chicken, breast, grilled",
                "brand": None,
                "source": "fatsecret",
                "food_id": "12345",
                "calories_100g": 165.0,
                "protein_100g": 31.0,
                "carbs_100g": 0.0,
                "fat_100g": 3.6,
            }
        ]

    async def get_food_details(self, fdc_id: int) -> dict[str, Any]:
        # Return USDA-style details with nutrient IDs
        return {
            "fdcId": fdc_id,
            "description": "Chicken, breast, grilled",
            "brandOwner": None,
            "servingSize": 100.0,
            "servingSizeUnit": "g",
            "foodNutrients": [
                {
                    "nutrient": {"id": 1008, "name": "Energy", "unitName": "cal"},
                    "amount": 165.0,
                },
                {
                    "nutrient": {"id": 1003, "name": "Protein", "unitName": "g"},
                    "amount": 31.0,
                },
                {
                    "nutrient": {"id": 1005, "name": "Carbohydrate", "unitName": "g"},
                    "amount": 0.0,
                },
                {
                    "nutrient": {
                        "id": 1004,
                        "name": "Total lipid (fat)",
                        "unitName": "g",
                    },
                    "amount": 3.6,
                },
            ],
            "foodPortions": [
                {
                    "measureUnit": {"name": "g"},
                    "gramWeight": 100.0,
                    "modifier": "serving",
                },
            ],
        }


class NoopFoodCacheService:
    async def get_cached_search(self, query: str) -> list[dict[str, Any]] | None:
        return None

    async def cache_search(
        self, query: str, results: list[dict[str, Any]], ttl: int = 3600
    ):
        return None

    async def get_cached_food(self, fdc_id: int) -> dict[str, Any] | None:
        return None

    async def cache_food(
        self, fdc_id: int, food_data: dict[str, Any], ttl: int = 86400
    ):
        return None


# Lightweight in-memory meal repository stub
class InMemoryMealRepository:
    def __init__(self):
        self._store: dict[str, Any] = {}

    async def save(self, meal):
        self._store[meal.meal_id] = meal
        return meal

    async def insert(self, meal):
        return await self.save(meal)

    async def find_by_id(self, meal_id: str):
        return self._store.get(meal_id)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_search_foods_query_handler_returns_mapped_results(monkeypatch):
    from src.app.handlers.query_handlers import SearchFoodsQueryHandler
    from src.app.queries.food.search_foods_query import SearchFoodsQuery
    from src.domain.services.food_mapping_service import FoodMappingService

    handler = SearchFoodsQueryHandler(
        fat_secret_service=StubFoodDataService(),
        cache_service=NoopFoodCacheService(),
        mapping_service=FoodMappingService(),
    )

    result = await handler.handle(SearchFoodsQuery(query="chicken", limit=10))

    assert isinstance(result, dict)
    assert "results" in result
    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["fdc_id"] is None
    assert item["food_id"] == "fatsecret:12345"
    assert item["name"].lower().startswith("chicken")
    assert item["data_type"] == "fatsecret"
    assert item["source"] == "fatsecret"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_food_details_query_handler_maps_nutrients():
    from src.app.handlers.query_handlers import GetFoodDetailsQueryHandler
    from src.app.queries.food.get_food_details_query import GetFoodDetailsQuery
    from src.domain.services.food_mapping_service import FoodMappingService

    handler = GetFoodDetailsQueryHandler(
        food_data_service=StubFoodDataService(),
        cache_service=NoopFoodCacheService(),
        mapping_service=FoodMappingService(),
    )

    result = await handler.handle(GetFoodDetailsQuery(fdc_id=12345))

    assert result["fdc_id"] == 12345
    assert result["name"].lower().startswith("chicken")
    assert result["serving_size"] == 100.0
    assert result["serving_unit"] == "g"
    assert result["macros"]["protein"] == 31.0
    assert result["macros"]["carbs"] == 0.0
    assert result["macros"]["fat"] == 3.6
    assert result["calories"] == 165.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_manual_meal_command_handler_aggregates_items(monkeypatch):
    # Arrange
    from unittest.mock import AsyncMock, MagicMock

    from src.app.commands.meal.create_manual_meal_command import (
        CreateManualMealCommand,
        CustomNutrition,
        ManualMealItem,
    )
    from src.app.handlers.command_handlers.create_manual_meal_command_handler import (
        CreateManualMealCommandHandler,
    )
    from src.domain.model import MealStatus

    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users = MagicMock()
    mock_uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    mock_uow.commit = AsyncMock()
    handler = CreateManualMealCommandHandler(
        uow=mock_uow,
        meal_repository=InMemoryMealRepository(),
    )

    # Chicken breast per 100g: 165 cal, 31g protein, 0g carbs, 3.6g fat
    chicken_nutrition = CustomNutrition(
        calories_per_100g=165.0,
        protein_per_100g=31.0,
        carbs_per_100g=0.0,
        fat_per_100g=3.6,
    )

    items = [
        ManualMealItem(
            fdc_id=12345,
            name="Chicken breast",
            quantity=150.0,
            unit="g",
            custom_nutrition=chicken_nutrition,
        ),  # 1.5x of 100g base
        ManualMealItem(
            fdc_id=12345,
            name="Chicken breast",
            quantity=50.0,
            unit="g",
            custom_nutrition=chicken_nutrition,
        ),  # 0.5x of 100g base
    ]
    command = CreateManualMealCommand(
        user_id="550e8400-e29b-41d4-a716-446655440001",
        items=items,
        dish_name="Manual Chicken Mix",
    )

    # Act
    meal = await handler.handle(command)

    # Assert
    assert meal.meal_id is not None
    assert meal.status == MealStatus.READY
    assert meal.dish_name == "Manual Chicken Mix"
    assert meal.nutrition is not None

    # Total quantity = 200g => 2x 100g base => calories and macros doubled
    assert meal.nutrition.calories == pytest.approx(
        312.8
    )  # derived: (31*4 + 0*4 + 3.6*9) * 2
    assert meal.nutrition.macros.protein == pytest.approx(62.0)
    assert meal.nutrition.macros.carbs == pytest.approx(0.0)
    assert meal.nutrition.macros.fat == pytest.approx(7.2)
