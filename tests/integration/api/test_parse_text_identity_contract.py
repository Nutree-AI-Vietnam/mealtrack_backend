import uuid
from datetime import datetime

import pytest

from src.api.mappers.meal_mapper import MealMapper
from src.api.routes.v1.meals_route_helpers import parsed_food_item_to_response
from src.app.commands.meal.parse_meal_text_command import ParseMealTextCommand
from src.app.handlers.command_handlers.parse_meal_text_handler import (
    ParseMealTextHandler,
)
from src.app.schemas.meal_schemas import ParsedFoodItemDto
from src.domain.model import FoodItem, Macros, Meal, MealImage, MealStatus, Nutrition
from src.domain.services.food_mapping_service import FoodMappingService
from src.domain.services.nutrition_integrity_policy import NutritionIntegrityError


def _provider_item() -> dict:
    return {
        "source": "fatsecret",
        "food_id": "fs-42",
        "description": "Rice",
        "protein_100g": 2.7,
        "carbs_100g": 28.0,
        "fat_100g": 0.3,
        "fiber_100g": 0.4,
        "sugar_100g": 0.1,
        "calories_100g": 126.1,
        "allowed_units": [{"unit": "g", "gram_weight": 100, "description": "100 g"}],
    }


def test_local_search_result_emits_one_canonical_origin_and_alias():
    result = FoodMappingService().map_search_item(
        {
            "source": "food_reference",
            "food_reference_id": 42,
            "is_verified": True,
            "description": "Rice",
            "protein_100g": 2.7,
            "carbs_100g": 28.0,
            "fat_100g": 0.3,
            "fiber_100g": 0.4,
            "sugar_100g": 0.1,
            "allowed_units": [{"unit": "g", "gram_weight": 1}],
        }
    )

    assert result["origin"] == "local"
    assert result["food_id"] == "food_reference:42"
    assert result["source_namespace"] == "food_reference"
    assert result["source_food_id"] == "42"
    assert result["nutrition_basis"] == "100g"
    assert result["nutrition_contract_version"] == "nutrition_integrity_v1"
    assert result["calories_per_100g"] == 124.7


def test_mismatching_local_alias_is_rejected():
    item = {
        "source": "food_reference",
        "food_reference_id": 42,
        "is_verified": True,
        "food_id": "food_reference:99",
        "description": "Rice",
        "protein_100g": 2.7,
        "carbs_100g": 28.0,
        "fat_100g": 0.3,
        "fiber_100g": 0.4,
        "sugar_100g": 0.1,
        "allowed_units": [{"unit": "g", "gram_weight": 1}],
    }

    try:
        FoodMappingService().map_search_item(item)
    except NutritionIntegrityError as exc:
        assert exc.result.reason_code == "origin_alias_mismatch"
    else:
        raise AssertionError("mismatching local alias must be rejected")


def test_provider_search_result_uses_namespaced_opaque_identity():
    result = FoodMappingService().map_search_item(_provider_item())

    assert result["origin"] == "provider"
    assert result["source_namespace"] == "fatsecret"
    assert result["source_food_id"] == "fs-42"
    assert result["food_id"] == "fatsecret:fs-42"
    assert result["allowed_units"] == [
        {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
        {"unit": "serving", "gram_weight": 100.0, "description": "100 g"},
    ]


def test_usda_search_result_uses_namespaced_fdc_identity():
    result = FoodMappingService().map_search_item(
        {
            "fdcId": 170000,
            "description": "Rice",
            "foodNutrients": [
                {"nutrientId": 1003, "value": 2.7},
                {"nutrientId": 1005, "value": 28.0},
                {"nutrientId": 1004, "value": 0.3},
                {"nutrientId": 1079, "value": 0.4},
                {"nutrientId": 2000, "value": 0.1},
                {"nutrientId": 1008, "value": 126.1},
            ],
        }
    )

    assert result["origin"] == "usda"
    assert result["source_namespace"] == "usda_fdc"
    assert result["source_food_id"] == "170000"
    assert result["food_id"] == "usda_fdc:170000"


def test_parse_response_keeps_legacy_fields_and_adds_identity_metadata():
    response = parsed_food_item_to_response(
        ParsedFoodItemDto(
            name="Rice",
            quantity=100,
            unit="g",
            protein=2.7,
            carbs=28.0,
            fat=0.3,
            fiber=0.4,
            origin="provider",
            food_id="fatsecret:fs-42",
            source_namespace="fatsecret",
            source_food_id="fs-42",
            nutrition_basis="100g",
            nutrition_contract_version="nutrition_integrity_v1",
            calories_per_100g=124.7,
        )
    )

    assert response.calories == 124.7
    assert response.origin == "provider"
    assert response.food_id == "fatsecret:fs-42"
    assert response.source_namespace == "fatsecret"
    assert response.source_food_id == "fs-42"
    assert response.calories_per_100g == 124.7
    assert response.protein_per_100g is None


class _RoundtripFatSecretProvider:
    async def search_food_candidates(self, _query: str, **_kwargs):
        return [
            {
                "food_id": "fs-rice",
                "food_name": "Rice, cooked",
                "food_type": "Generic",
            }
        ]

    async def get_food_details(self, food_id: str, **_kwargs):
        return {
            "food_id": food_id,
            "food_name": "Rice, cooked",
            "protein_100g": 2.7,
            "carbs_100g": 28.0,
            "fat_100g": 0.3,
            "fiber_100g": 0.4,
            "sugar_100g": 0.1,
            "calories_100g": 126.1,
            "metric_serving_amount": 100,
            "allowed_units": [{"unit": "g", "gram_weight": 100, "description": "100 g"}],
        }


class _RoundtripAI:
    async def generate_meal_plan_async(self, **_kwargs):
        return {
            "items": [
                {
                    "name": "Rice",
                    "lookup_name": "rice",
                    "quantity": 100,
                    "quantity_g": 100,
                    "unit": "g",
                    "macros": {"protein_g": 2.7, "carbs_g": 28.0, "fat_g": 0.3},
                }
            ]
        }


class _RoundtripAdoptRepo:
    async def adopt_provider_food(
        self, namespace, food_id, english_name, per_100g, servings, locale, locale_name
    ):
        return {"id": 42}


class _RoundtripUow:
    def __init__(self, food_references):
        self.food_references = food_references

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_parse_save_get_roundtrip_uses_catalog_names_and_snapshot_kcal():
    """Parse → persisted item → GET shows locale catalog names with frozen snapshot macros."""
    handler = ParseMealTextHandler(
        meal_generation_service=_RoundtripAI(),
        fat_secret_service=_RoundtripFatSecretProvider(),
        uow_factory=lambda: _RoundtripUow(_RoundtripAdoptRepo()),
        structured_reference_enabled=True,
    )
    parsed = await handler.handle(
        ParseMealTextCommand(text="100g rice", user_id="user-1", language="en")
    )
    assert parsed.items
    assert parsed.items[0].food_reference_id == 42
    saved_item = FoodItem(
        id="item-1",
        name=parsed.items[0].name,
        quantity=parsed.items[0].quantity,
        unit=parsed.items[0].unit,
        macros=Macros(
            protein=parsed.items[0].protein,
            carbs=parsed.items[0].carbs,
            fat=parsed.items[0].fat,
        ),
        food_reference_id=parsed.items[0].food_reference_id,
        source_snapshot={
            "basis": "100g",
            "protein_per_100g": 2.7,
            "carbs_per_100g": 28.0,
            "fat_per_100g": 0.3,
            "canonical_name": "Rice, cooked",
        },
    )
    meal = Meal(
        meal_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        status=MealStatus.READY,
        image=MealImage(
            url="https://example.com/meal.jpg",
            image_id=str(uuid.uuid4()),
            format="jpeg",
            size_bytes=1024,
        ),
        dish_name="Rice bowl",
        created_at=datetime(2026, 8, 22),
        ready_at=datetime(2026, 8, 22),
        nutrition=Nutrition(macros=saved_item.macros, food_items=[saved_item]),
    )
    catalog_projection = {
        42: {
            "name": "Rice, cooked",
            "name_vi": "Cơm trắng",
        }
    }

    en = MealMapper.to_detailed_response(
        meal,
        target_language="en",
        display_name_by_food_reference=catalog_projection,
    )
    vi = MealMapper.to_detailed_response(
        meal,
        target_language="vi",
        display_name_by_food_reference=catalog_projection,
    )

    assert en.food_items[0].name == "Rice, cooked"
    assert vi.food_items[0].name == "Cơm trắng"
    assert en.food_items[0].nutrition.protein_g == pytest.approx(2.7)
    assert vi.food_items[0].nutrition.protein_g == pytest.approx(2.7)
