import pytest

from src.domain.services.food_mapping_service import FoodMappingService
from src.domain.services.nutrition_integrity_policy import NutritionIntegrityError


def test_map_fdc_barcode_product_returns_flat_barcode_shape():
    service = FoodMappingService()

    result = service.map_fdc_barcode_product(
        {
            "fdcId": 1,
            "description": "Test Cereal",
            "brandOwner": "Test Brand",
            "servingSize": 30,
            "servingSizeUnit": "g",
            "foodNutrients": [
                {"nutrientId": 1003, "value": 8},
                {"nutrientId": 1005, "value": 72},
                {"nutrientId": 1004, "value": 2.5},
                {"nutrientId": 1079, "value": 6},
                {"nutrientId": 2000, "value": 18},
            ],
        },
        barcode="00036000291452",
    )

    assert result["name"] == "Test Cereal"
    assert result["brand"] == "Test Brand"
    assert result["barcode"] == "00036000291452"
    assert result["protein_100g"] == 8
    assert result["carbs_100g"] == 72
    assert result["fat_100g"] == 2.5
    assert result["fiber_100g"] == 6
    assert result["sugar_100g"] == 18
    assert result["source"] == "usda_fdc"
    assert result["source_namespace"] == "usda_fdc"
    assert result["source_food_id"] == "1"
    assert result["is_verified"] is True
    assert result["allowed_units"] == [
        {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
        {"unit": "serving", "gram_weight": 30.0, "description": "1 serving (30 g)"},
    ]


def test_map_search_item_rejects_catastrophic_provider_nutrition():
    with pytest.raises(NutritionIntegrityError, match="macro_mass_out_of_range"):
        FoodMappingService().map_search_item(
            {
                "source": "fatsecret",
                "food_id": "potato-bad",
                "description": "Potato",
                "protein_100g": 100,
                "carbs_100g": 100,
                "fat_100g": 100,
                "fiber_100g": 0,
                "sugar_100g": 0,
                "calories_100g": 1500,
            }
        )


def test_map_search_item_keeps_fatsecret_hit_without_macros():
    result = FoodMappingService().map_search_item(
        {
            "source": "fatsecret",
            "food_id": "1",
            "source_namespace": "fatsecret",
            "source_food_id": "1",
            "description": "Mystery soup",
        }
    )

    assert result["name"] == "Mystery soup"
    assert result["origin"] == "provider"
    assert result["food_id"] == "fatsecret:1"
    assert result["nutrients"]["protein"] is None
    assert result["custom_nutrition"] is None


def test_map_search_item_treats_adopted_fatsecret_hit_as_local_catalog():
    result = FoodMappingService().map_search_item(
        {
            "source": "fatsecret",
            "food_id": "12345",
            "source_namespace": "fatsecret",
            "source_food_id": "12345",
            "food_reference_id": 777,
            "description": "Banana",
            "protein_100g": 1.1,
            "carbs_100g": 23.0,
            "fat_100g": 0.3,
            "fiber_100g": 2.6,
            "sugar_100g": 12.2,
            "allowed_units": [
                {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
            ],
        }
    )

    assert result["origin"] == "local"
    assert result["food_reference_id"] == 777
    assert result["source"] == "food_reference"
    assert result["food_id"] == "food_reference:777"


def test_map_search_item_rejects_unverified_local_reference():
    with pytest.raises(NutritionIntegrityError, match="unverified_reference"):
        FoodMappingService().map_search_item(
            {
                "source": "food_reference",
                "food_reference_id": 7,
                "is_verified": False,
                "description": "Rice",
                "protein_100g": 2.7,
                "carbs_100g": 28.0,
                "fat_100g": 0.3,
                "fiber_100g": 0.4,
                "sugar_100g": 0.1,
            }
        )
