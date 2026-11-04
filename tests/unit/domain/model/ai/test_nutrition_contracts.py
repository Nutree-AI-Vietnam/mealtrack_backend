"""Tests for canonical AI nutrition output contracts."""

import pytest
from pydantic import ValidationError

from src.domain.model.ai.nutrition_contracts import (
    AINutritionMacros,
    BeverageMetadata,
    FoodLabelNutritionResponse,
    MealTextNutritionResponse,
    VisionNutritionResponse,
)


def _valid_macros() -> dict[str, float]:
    return {
        "protein_g": 35.0,
        "carbs_g": 42.0,
        "fat_g": 12.0,
        "fiber_g": 4.0,
        "sugar_g": 3.0,
    }


class TestAINutritionMacros:
    def test_accepts_macro_grams(self):
        macros = AINutritionMacros.model_validate(_valid_macros())

        assert macros.protein_g == pytest.approx(35.0)
        assert macros.fiber_g == pytest.approx(4.0)
        assert macros.sugar_g == pytest.approx(3.0)

    def test_defaults_optional_fiber_and_sugar_to_zero(self):
        macros = AINutritionMacros.model_validate(
            {"protein_g": 10.0, "carbs_g": 20.0, "fat_g": 5.0}
        )

        assert macros.fiber_g == pytest.approx(0.0)
        assert macros.sugar_g == pytest.approx(0.0)

    def test_rejects_negative_macros(self):
        with pytest.raises(ValidationError):
            AINutritionMacros.model_validate(
                {"protein_g": -1.0, "carbs_g": 20.0, "fat_g": 5.0}
            )


class TestVisionNutritionResponse:
    def test_accepts_realistic_food_quantities(self):
        response = VisionNutritionResponse.model_validate(
            {
                "dish_name": "Chicken rice bowl",
                "foods": [
                    {
                        "name": "Grilled chicken",
                        "quantity_g": 150.0,
                        "macros": _valid_macros(),
                        "confidence": 0.92,
                    }
                ],
                "confidence": 0.88,
            }
        )

        assert response.dish_name == "Chicken rice bowl"
        assert response.is_food is True
        assert response.foods[0].quantity_g == pytest.approx(150.0)
        assert response.foods[0].macros.protein_g == pytest.approx(35.0)
        assert response.emoji is None

    def test_accepts_optional_emoji(self):
        response = VisionNutritionResponse.model_validate(
            {
                "dish_name": "Chicken rice bowl",
                "emoji": "🍚",
                "foods": [
                    {
                        "name": "Grilled chicken",
                        "quantity_g": 150.0,
                        "macros": _valid_macros(),
                        "confidence": 0.92,
                    }
                ],
                "confidence": 0.88,
            }
        )

        assert response.emoji == "🍚"

    def test_rejects_extra_top_level_fields(self):
        with pytest.raises(ValidationError):
            VisionNutritionResponse.model_validate(
                {
                    "dish_name": "Chicken rice bowl",
                    "foods": [
                        {
                            "name": "Grilled chicken",
                            "quantity_g": 150.0,
                            "macros": _valid_macros(),
                        }
                    ],
                    "confidence": 0.88,
                    "calories": 9999,
                }
            )

    def test_rejects_extra_food_fields(self):
        with pytest.raises(ValidationError):
            VisionNutritionResponse.model_validate(
                {
                    "dish_name": "Chicken rice bowl",
                    "foods": [
                        {
                            "name": "Grilled chicken",
                            "quantity_g": 150.0,
                            "unit": "g",
                            "macros": _valid_macros(),
                        }
                    ],
                    "confidence": 0.88,
                }
            )

    def test_rejects_extra_macro_fields(self):
        macros = _valid_macros()
        macros["calories"] = 9999.0

        with pytest.raises(ValidationError):
            VisionNutritionResponse.model_validate(
                {
                    "dish_name": "Chicken rice bowl",
                    "foods": [
                        {
                            "name": "Grilled chicken",
                            "quantity_g": 150.0,
                            "macros": macros,
                        }
                    ],
                }
            )

    def test_accepts_non_food_with_empty_foods(self):
        response = VisionNutritionResponse.model_validate(
            {
                "is_food": False,
                "dish_name": None,
                "foods": [],
                "confidence": 0.95,
            }
        )

        assert response.is_food is False
        assert response.foods == []

    def test_rejects_impossible_quantity_g(self):
        with pytest.raises(ValidationError):
            VisionNutritionResponse.model_validate(
                {
                    "foods": [
                        {
                            "name": "Rice",
                            "quantity_g": 150000.0,
                            "macros": _valid_macros(),
                        }
                    ]
                }
            )

    def test_rejects_empty_food_name(self):
        with pytest.raises(ValidationError):
            VisionNutritionResponse.model_validate(
                {
                    "foods": [
                        {
                            "name": " ",
                            "quantity_g": 120.0,
                            "macros": _valid_macros(),
                        }
                    ]
                }
            )

    def test_rejects_more_than_twenty_foods(self):
        with pytest.raises(ValidationError):
            VisionNutritionResponse.model_validate(
                {
                    "foods": [
                        {
                            "name": f"Food {index}",
                            "quantity_g": 10.0,
                            "macros": _valid_macros(),
                        }
                        for index in range(21)
                    ]
                }
            )

    def test_rejects_empty_foods_when_is_food_defaults_true(self):
        with pytest.raises(ValidationError):
            VisionNutritionResponse.model_validate({"foods": []})

    def test_rejects_missing_foods(self):
        with pytest.raises(ValidationError):
            VisionNutritionResponse.model_validate({"dish_name": "Unknown meal"})

    def test_rejects_beverage_metadata_for_meal_scan_output(self):
        with pytest.raises(ValidationError, match="beverage_metadata is not accepted"):
            VisionNutritionResponse.model_validate(
                {
                    "is_food": True,
                    "dish_name": "Coca-Cola 330ml Can",
                    "foods": [
                        {
                            "name": "Coca-Cola",
                            "quantity_g": 330,
                            "macros": _valid_macros(),
                        }
                    ],
                    "confidence": 0.95,
                    "beverage_metadata": {
                        "is_packaged_beverage": True,
                        "brand": "Coca-Cola",
                        "product_name": "Coca-Cola Original",
                        "container_type": "can",
                        "volume_ml": 330,
                        "sugar_per_100ml": 10.6,
                        "kcal_per_100ml": 42.0,
                        "label_source": "nutrition_panel",
                    },
                }
            )

    def test_rejects_metadata_only_packaged_beverage_output(self):
        with pytest.raises(ValidationError, match="beverage_metadata is not accepted"):
            VisionNutritionResponse.model_validate(
                {
                    "is_food": True,
                    "dish_name": "Pocari Sweat",
                    "foods": [],
                    "confidence": 0.88,
                    "beverage_metadata": {
                        "is_packaged_beverage": True,
                        "brand": "Pocari Sweat",
                        "container_type": "bottle",
                        "volume_ml": 500,
                        "kcal_per_100ml": 25.0,
                        "sugar_per_100ml": 6.2,
                        "label_source": "estimate",
                    },
                }
            )

    def test_rejects_extra_beverage_metadata_fields(self):
        with pytest.raises(ValidationError):
            VisionNutritionResponse.model_validate(
                {
                    "is_food": False,
                    "foods": [],
                    "beverage_metadata": {
                        "is_packaged_beverage": True,
                        "brand": "Coca-Cola",
                        "unknown_field": "boom",
                    },
                }
            )

    def test_brand_max_length_100_enforced(self):
        with pytest.raises(ValidationError):
            BeverageMetadata.model_validate(
                {
                    "is_packaged_beverage": True,
                    "brand": "X" * 101,
                }
            )


class TestFoodLabelNutritionResponse:
    def test_accepts_food_label_serving_contract(self):
        response = FoodLabelNutritionResponse.model_validate(
            {
                "product_name": "Cereal",
                "brand": "Acme",
                "serving_size": {"display_text": "2/3 cup (55g)", "grams": 55},
                "servings_per_package": 8,
                "label_calories_per_serving": 230,
                "macros_per_serving": _valid_macros(),
                "confidence": 0.91,
                "label_notes": ["Includes 10g added sugars"],
            }
        )

        assert response.is_food_label is True
        assert response.product_name == "Cereal"
        assert response.serving_size.grams == pytest.approx(55)
        assert response.servings_per_package == pytest.approx(8)
        assert response.macros_per_serving.protein_g == pytest.approx(35)

    def test_rejects_missing_serving_grams(self):
        with pytest.raises(ValidationError):
            FoodLabelNutritionResponse.model_validate(
                {
                    "product_name": "Cereal",
                    "serving_size": {"display_text": "2/3 cup"},
                    "servings_per_package": 8,
                    "macros_per_serving": _valid_macros(),
                }
            )

    def test_defaults_unreadable_package_metadata(self):
        response = FoodLabelNutritionResponse.model_validate(
            {
                "product_name": None,
                "serving_size": None,
                "servings_per_package": None,
                "macros_per_serving": _valid_macros(),
            }
        )

        assert response.product_name == "Scanned Food Label"
        assert response.serving_size.display_text == "100g"
        assert response.serving_size.grams == pytest.approx(100)
        assert response.servings_per_package == pytest.approx(1)

    def test_rejects_missing_macros(self):
        with pytest.raises(ValidationError):
            FoodLabelNutritionResponse.model_validate(
                {
                    "product_name": "Cereal",
                    "serving_size": {"display_text": "2/3 cup", "grams": 55},
                    "servings_per_package": 8,
                }
            )

    def test_accepts_unbounded_label_notes(self):
        response = FoodLabelNutritionResponse.model_validate(
            {
                "product_name": "Cereal",
                "serving_size": {"display_text": "2/3 cup (55g)", "grams": 55},
                "servings_per_package": 8,
                "macros_per_serving": _valid_macros(),
                "label_notes": [
                    " Calories per serving: 120 ",
                    "Total fat: 3g",
                    "Sodium: 140mg",
                    "Carbohydrates: 24g",
                    "Protein: 2g",
                    "Calcium: 10mg",
                    "Iron: 1mg",
                    "Potassium: 50mg",
                ],
            }
        )

        assert response.label_notes == [
            "Calories per serving: 120",
            "Total fat: 3g",
            "Sodium: 140mg",
            "Carbohydrates: 24g",
            "Protein: 2g",
            "Calcium: 10mg",
            "Iron: 1mg",
            "Potassium: 50mg",
        ]


class TestMealTextNutritionResponse:
    def test_accepts_display_quantity_unit_and_optional_quantity_g(self):
        response = MealTextNutritionResponse.model_validate(
            {
                "emoji": "🍜",
                "items": [
                    {
                        "name": "Pho",
                        "quantity": 1.0,
                        "unit": "bowl",
                        "english_unit": "bowl",
                        "quantity_g": 550.0,
                        "macros": _valid_macros(),
                        "calories": 9999,
                    }
                ],
            }
        )

        assert response.emoji == "🍜"
        assert response.items[0].quantity == pytest.approx(1.0)
        assert response.items[0].unit == "bowl"
        assert response.items[0].quantity_g == pytest.approx(550.0)
        assert "calories" not in response.model_dump()["items"][0]

    def test_accepts_missing_quantity_g_for_display_only_items(self):
        response = MealTextNutritionResponse.model_validate(
            {
                "items": [
                    {
                        "name": "Egg",
                        "quantity": 2.0,
                        "unit": "piece",
                        "macros": _valid_macros(),
                    }
                ]
            }
        )

        assert response.items[0].quantity_g is None

    def test_folds_current_flat_macro_shape_into_canonical_macros(self):
        response = MealTextNutritionResponse.model_validate(
            {
                "items": [
                    {
                        "name": "Eggs",
                        "quantity": 2.0,
                        "unit": "large",
                        "english_unit": "large",
                        "protein": 12.6,
                        "carbs": 0.7,
                        "fat": 9.5,
                        "calories": 144,
                    }
                ]
            }
        )

        item = response.items[0]
        assert item.macros.protein_g == pytest.approx(12.6)
        assert item.macros.carbs_g == pytest.approx(0.7)
        assert item.macros.fat_g == pytest.approx(9.5)
        assert "protein" not in item.model_dump()
        assert "calories" not in item.model_dump()

    def test_text_macros_ignore_nested_extra_fields(self):
        response = MealTextNutritionResponse.model_validate(
            {
                "items": [
                    {
                        "name": "Pho",
                        "quantity": 1.0,
                        "unit": "bowl",
                        "macros": {
                            "protein_g": 20.0,
                            "carbs_g": 40.0,
                            "fat_g": 10.0,
                            "calories": 9999,
                        },
                    }
                ]
            }
        )

        assert response.items[0].macros.protein_g == pytest.approx(20.0)
        assert "calories" not in response.items[0].macros.model_dump()

    def test_rejects_empty_items(self):
        with pytest.raises(ValidationError):
            MealTextNutritionResponse.model_validate({"items": []})

    def test_rejects_malformed_items(self):
        with pytest.raises(ValidationError):
            MealTextNutritionResponse.model_validate({"items": {"name": "Pho"}})
