"""Unit tests for curated popular staples query handler."""

import pytest

from src.app.handlers.query_handlers.get_popular_staples_query_handler import (
    GetPopularStaplesQueryHandler,
)
from src.app.queries.food.get_popular_staples_query import (
    POPULAR_STAPLE_FOOD_REFERENCE_IDS,
    GetPopularStaplesQuery,
)
from src.domain.services.food_mapping_service import FoodMappingService


def _row(ref_id: int, name: str, name_vi: str | None = None) -> dict:
    return {
        "id": ref_id,
        "name": name,
        "name_vi": name_vi,
        "brand": None,
        "source": "fatsecret",
        "source_namespace": "fatsecret",
        "source_food_id": str(ref_id),
        "is_verified": True,
        "serving_size": None,
        "protein_100g": 10.0,
        "carbs_100g": 0.0,
        "fat_100g": 5.0,
        "fiber_100g": 0.0,
        "sugar_100g": 0.0,
        "allowed_units": [
            {
                "unit": "g",
                "gram_weight": 1.0,
                "description": "1 g",
            },
            {
                "unit": "oz, boneless, cooked",
                "gram_weight": 28.35,
                "description": "oz, boneless, cooked",
                "display_description": "Oz, không xương, đã nấu",
            },
            {
                "unit": "ml",
                "gram_weight": 103.133,
                "description": "ml",
            },
        ],
    }


@pytest.mark.asyncio
async def test_popular_staples_preserve_order_and_localize_vi():
    rows = [
        _row(205, "Beef", "Thịt bò"),
        _row(294, "Pork", "Thịt lợn"),
        _row(348, "White Rice", "Cơm"),
        _row(363, "Egg", "Trứng"),
        _row(440, "Whole Milk", "Sữa tươi nguyên kem"),
    ]

    async def load(_ids):
        return rows

    handler = GetPopularStaplesQueryHandler(FoodMappingService(), load)
    result = await handler.handle(GetPopularStaplesQuery(language="vi"))

    assert result["total"] == 5
    assert [item["food_reference_id"] for item in result["results"]] == list(
        POPULAR_STAPLE_FOOD_REFERENCE_IDS
    )
    assert [item["name"] for item in result["results"]] == [
        "Thịt bò",
        "Thịt lợn",
        "Cơm",
        "Trứng",
        "Sữa tươi nguyên kem",
    ]
    beef_units = result["results"][0]["allowed_units"]
    oz = next(unit for unit in beef_units if unit["unit"] == "oz, boneless, cooked")
    assert oz["display_description"] == "Oz, không xương, đã nấu"
    ml = next(unit for unit in beef_units if unit["unit"] == "ml")
    assert ml["description"] == "100 ml"


@pytest.mark.asyncio
async def test_popular_staples_strip_vi_labels_for_english():
    async def load(_ids):
        return [_row(205, "Beef", "Thịt bò")]

    handler = GetPopularStaplesQueryHandler(FoodMappingService(), load)
    result = await handler.handle(GetPopularStaplesQuery(language="en"))

    assert result["results"][0]["name"] == "Beef"
    oz = next(
        unit
        for unit in result["results"][0]["allowed_units"]
        if unit["unit"] == "oz, boneless, cooked"
    )
    assert "display_description" not in oz
