"""Unit tests for curated popular staples query handler."""

import pytest

from src.app.handlers.query_handlers.get_popular_staples_query_handler import (
    GetPopularStaplesQueryHandler,
)
from src.app.queries.food.get_popular_staples_query import (
    POPULAR_STAPLE_SOURCE_IDENTITIES,
    GetPopularStaplesQuery,
)
from src.domain.services.food_mapping_service import FoodMappingService


def _row(
    ref_id: int,
    name: str,
    *,
    source_food_id: str,
    name_vi: str | None = None,
) -> dict:
    return {
        "id": ref_id,
        "name": name,
        "name_vi": name_vi,
        "brand": None,
        "source": "fatsecret",
        "source_namespace": "fatsecret",
        "source_food_id": source_food_id,
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
        _row(1933, "Beef", source_food_id="1350", name_vi="Thịt bò"),
        _row(1934, "Pork", source_food_id="1421", name_vi="Thịt lợn"),
        _row(1885, "White Rice", source_food_id="4501", name_vi="Cơm"),
        _row(1887, "Egg", source_food_id="3092", name_vi="Trứng"),
        _row(1886, "Whole Milk", source_food_id="794", name_vi="Sữa tươi nguyên kem"),
    ]

    async def load(_identities):
        return rows

    handler = GetPopularStaplesQueryHandler(FoodMappingService(), load)
    result = await handler.handle(GetPopularStaplesQuery(language="vi"))

    assert result["total"] == 5
    assert [item["source_food_id"] for item in result["results"]] == [
        food_id for _, food_id in POPULAR_STAPLE_SOURCE_IDENTITIES
    ]
    assert [item["source_namespace"] for item in result["results"]] == [
        "fatsecret"
    ] * 5
    assert [item["food_reference_id"] for item in result["results"]] == [
        1933,
        1934,
        1885,
        1887,
        1886,
    ]
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
    async def load(_identities):
        return [_row(1933, "Beef", source_food_id="1350", name_vi="Thịt bò")]

    handler = GetPopularStaplesQueryHandler(FoodMappingService(), load)
    result = await handler.handle(GetPopularStaplesQuery(language="en"))

    assert result["results"][0]["name"] == "Beef"
    oz = next(
        unit
        for unit in result["results"][0]["allowed_units"]
        if unit["unit"] == "oz, boneless, cooked"
    )
    assert "display_description" not in oz


def test_normalize_ml_portion_only_rewrites_trivial_labels():
    normalize = GetPopularStaplesQueryHandler._normalize_ml_portion

    assert normalize(
        {"unit": "ml", "gram_weight": 103.133, "description": "ml"}
    )["description"] == "100 ml"
    assert normalize(
        {"unit": "ml", "gram_weight": 103.133, "description": "1 ml"}
    )["description"] == "100 ml"
    assert normalize(
        {"unit": "ml", "gram_weight": 250.0, "description": "250 ml"}
    )["description"] == "250 ml"
    assert normalize(
        {"unit": "ml", "gram_weight": 1.0, "description": "ml"}
    )["description"] == "ml"
