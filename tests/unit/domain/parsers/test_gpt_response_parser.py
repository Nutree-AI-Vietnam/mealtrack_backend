"""Tests for GPTResponseParser behavior."""

import pytest

from src.domain.parsers.gpt_response_parser import GPTResponseParsingError


def test_parse_to_nutrition_accepts_canonical_quantity_g(gpt_parser):
    gpt_response = {
        "structured_data": {
            "is_food": True,
            "dish_name": "Chicken rice bowl",
            "emoji": "🍚",
            "foods": [
                {
                    "name": "Grilled chicken",
                    "quantity_g": 150.0,
                    "macros": {
                        "protein_g": 35.0,
                        "carbs_g": 0.0,
                        "fat_g": 5.0,
                        "fiber_g": 0.0,
                        "sugar_g": 0.0,
                    },
                    "confidence": 0.92,
                }
            ],
            "confidence": 0.88,
        }
    }

    nutrition = gpt_parser.parse_to_nutrition(gpt_response)

    assert nutrition.food_items[0].quantity == 150.0
    assert nutrition.food_items[0].unit == "g"
    assert nutrition.food_items[0].macros.protein == 35.0
    assert nutrition.confidence_score == 0.88


def test_parse_to_nutrition_rejects_legacy_quantity_unit_shape(gpt_parser):
    gpt_response = {
        "structured_data": {
            "is_food": True,
            "dish_name": "Chicken rice bowl",
            "foods": [
                {
                    "name": "Grilled chicken",
                    "quantity": 150.0,
                    "unit": "g",
                    "macros": {
                        "protein": 35.0,
                        "carbs": 0.0,
                        "fat": 5.0,
                    },
                }
            ],
            "confidence": 0.88,
        }
    }

    with pytest.raises(GPTResponseParsingError):
        gpt_parser.parse_to_nutrition(gpt_response)


def test_parse_emoji_reads_canonical_emoji(gpt_parser):
    gpt_response = {
        "structured_data": {
            "is_food": True,
            "dish_name": "Chicken rice bowl",
            "emoji": "🍚",
            "foods": [
                {
                    "name": "Grilled chicken",
                    "quantity_g": 150.0,
                    "macros": {
                        "protein_g": 35.0,
                        "carbs_g": 0.0,
                        "fat_g": 5.0,
                    },
                }
            ],
        }
    }

    assert gpt_parser.parse_emoji(gpt_response) == "🍚"


class TestGPTResponseParserFoodLimit:
    @pytest.mark.parametrize("count", [9, 20])
    def test_keeps_small_components_beyond_eight_rows(self, gpt_parser, count):
        foods = [
            {
                "name": f"Visible component {index}",
                "quantity_g": 0.5,
                "macros": {"protein_g": 0, "carbs_g": 0.1, "fat_g": 0},
                "confidence": 0.6,
            }
            for index in range(count)
        ]
        nutrition = gpt_parser.parse_to_nutrition(
            {"structured_data": {"is_food": True, "foods": foods}}
        )
        assert [food.name for food in nutrition.food_items] == [
            food["name"] for food in foods
        ]
        assert nutrition.macros.carbs == pytest.approx(count * 0.1)

    def test_rejects_food_items_over_max(self, gpt_parser):
        foods = [
            {
                "name": f"Food {i}",
                "quantity_g": 1,
                "macros": {"protein_g": 1, "carbs_g": 2, "fat_g": 3},
                "confidence": 0.8,
            }
            for i in range(21)
        ]

        gpt_response = {
            "structured_data": {
                "dish_name": "Test Dish",
                "foods": foods,
                "confidence": 0.9,
            }
        }

        with pytest.raises(GPTResponseParsingError):
            gpt_parser.parse_to_nutrition(gpt_response)


class TestGPTResponseParserDishNameCompatibility:
    def test_parse_to_nutrition_allows_missing_dish_name(self, gpt_parser):
        gpt_response = {
            "structured_data": {
                "foods": [
                    {
                        "name": "Food 0",
                        "quantity_g": 1,
                        "macros": {"protein_g": 1, "carbs_g": 2, "fat_g": 3},
                        "confidence": 0.8,
                    }
                ],
                "confidence": 0.9,
            }
        }

        nutrition = gpt_parser.parse_to_nutrition(gpt_response)

        assert nutrition.food_items is not None
        assert len(nutrition.food_items) == 1
        assert nutrition.food_items[0].name == "Food 0"
        assert nutrition.macros.protein == pytest.approx(1.0)
        assert nutrition.macros.carbs == pytest.approx(2.0)
        assert nutrition.macros.fat == pytest.approx(3.0)


class TestGPTResponseParserOptionalFoods:
    def test_parse_to_nutrition_rejects_missing_foods_for_food_image(self, gpt_parser):
        gpt_response = {
            "structured_data": {
                "dish_name": "Macro Meal",
            }
        }

        with pytest.raises(GPTResponseParsingError):
            gpt_parser.parse_to_nutrition(gpt_response)

    def test_parse_to_nutrition_allows_non_food_without_foods(self, gpt_parser):
        gpt_response = {
            "structured_data": {
                "is_food": False,
                "dish_name": "Macro Meal",
                "foods": [],
                "confidence": 0.8,
            }
        }

        nutrition = gpt_parser.parse_to_nutrition(gpt_response)

        assert nutrition.food_items is None
        assert nutrition.macros.protein == pytest.approx(0.0)
        assert nutrition.macros.carbs == pytest.approx(0.0)
        assert nutrition.macros.fat == pytest.approx(0.0)
        assert nutrition.confidence_score == pytest.approx(0.8)

    def test_parse_to_nutrition_reads_top_level_confidence(self, gpt_parser):
        gpt_response = {
            "structured_data": {
                "foods": [
                    {
                        "name": "Food 0",
                        "quantity_g": 1,
                        "macros": {"protein_g": 1, "carbs_g": 2, "fat_g": 3},
                    }
                ],
                "confidence": 0.72,
            }
        }

        nutrition = gpt_parser.parse_to_nutrition(gpt_response)

        assert nutrition.confidence_score == pytest.approx(0.72)


class TestGPTResponseParserFoodGuard:
    def test_parse_is_food_defaults_true_when_missing(self, gpt_parser):
        gpt_response = {"structured_data": {"dish_name": "Rice Bowl"}}

        assert gpt_parser.parse_is_food(gpt_response) is True

    def test_parse_is_food_returns_false_for_boolean_false(self, gpt_parser):
        gpt_response = {"structured_data": {"is_food": False}}

        assert gpt_parser.parse_is_food(gpt_response) is False

    def test_parse_is_food_returns_false_for_string_false(self, gpt_parser):
        gpt_response = {"structured_data": {"is_food": "false"}}

        assert gpt_parser.parse_is_food(gpt_response) is False

    def test_parse_is_food_returns_false_for_numeric_zero(self, gpt_parser):
        gpt_response = {"structured_data": {"is_food": 0}}

        assert gpt_parser.parse_is_food(gpt_response) is False

    def test_parse_is_food_defaults_true_when_structured_data_missing(self, gpt_parser):
        assert gpt_parser.parse_is_food({}) is True


class TestGPTResponseParserStrictSchemaMode:
    def test_non_strict_mode_rejects_non_list_foods(self):
        from src.domain.parsers.gpt_response_parser import GPTResponseParser

        parser = GPTResponseParser(strict_schema_mode=False)
        gpt_response = {
            "structured_data": {
                "dish_name": "Macro Meal",
                "foods": "invalid-shape",
            }
        }

        with pytest.raises(GPTResponseParsingError):
            parser.parse_to_nutrition(gpt_response)


class TestGPTResponseParserZeroQuantity:
    def test_rejects_zero_quantity_food_items(self, gpt_parser):
        """AI quantity=0 should fail validation instead of producing partial meals."""
        gpt_response = {
            "structured_data": {
                "dish_name": "Mixed Plate",
                "foods": [
                    {
                        "name": "Valid Food",
                        "quantity_g": 100,
                        "macros": {"protein_g": 10, "carbs_g": 20, "fat_g": 5},
                    },
                    {
                        "name": "Zero Quantity Food",
                        "quantity_g": 0,
                        "macros": {"protein_g": 5, "carbs_g": 10, "fat_g": 2},
                    },
                    {
                        "name": "Another Valid Food",
                        "quantity_g": 50,
                        "macros": {"protein_g": 8, "carbs_g": 15, "fat_g": 3},
                    },
                ],
                "confidence": 0.9,
            }
        }

        with pytest.raises(GPTResponseParsingError):
            gpt_parser.parse_to_nutrition(gpt_response)

    def test_rejects_negative_quantity_food_items(self, gpt_parser):
        """Negative quantities should fail validation."""
        gpt_response = {
            "structured_data": {
                "dish_name": "Test Meal",
                "foods": [
                    {
                        "name": "Negative Quantity",
                        "quantity_g": -5,
                        "macros": {"protein_g": 5, "carbs_g": 10, "fat_g": 2},
                    },
                    {
                        "name": "Valid Food",
                        "quantity_g": 100,
                        "macros": {"protein_g": 10, "carbs_g": 20, "fat_g": 5},
                    },
                ],
                "confidence": 0.9,
            }
        }

        with pytest.raises(GPTResponseParsingError):
            gpt_parser.parse_to_nutrition(gpt_response)

    def test_rejects_over_max_quantity_food_items(self, gpt_parser):
        """Impossible AI quantities should fail validation."""
        gpt_response = {
            "structured_data": {
                "dish_name": "Mixed Plate",
                "foods": [
                    {
                        "name": "Valid Food",
                        "quantity_g": 100,
                        "macros": {"protein_g": 10, "carbs_g": 20, "fat_g": 5},
                    },
                    {
                        "name": "Impossible Quantity",
                        "quantity_g": 150000,
                        "macros": {
                            "protein_g": 500,
                            "carbs_g": 1000,
                            "fat_g": 200,
                        },
                    },
                ],
                "confidence": 0.9,
            }
        }

        with pytest.raises(GPTResponseParsingError):
            gpt_parser.parse_to_nutrition(gpt_response)


class TestGPTResponseParserQuantityGMapping:
    def test_valid_quantity_g_maps_to_food_item_with_grams_unit(self, gpt_parser):
        """quantity_g=100 maps to FoodItem(quantity=100, unit='g')."""
        gpt_response = {
            "structured_data": {
                "dish_name": "Grilled Chicken",
                "foods": [
                    {
                        "name": "Chicken breast",
                        "quantity_g": 100,
                        "macros": {
                            "protein_g": 30.0,
                            "carbs_g": 0.0,
                            "fat_g": 6.0,
                        },
                        "confidence": 0.9,
                    }
                ],
                "confidence": 0.85,
            }
        }

        nutrition = gpt_parser.parse_to_nutrition(gpt_response)

        assert nutrition.food_items is not None
        assert len(nutrition.food_items) == 1
        item = nutrition.food_items[0]
        assert item.quantity == pytest.approx(100.0)
        assert item.unit == "g"

    def test_calories_derived_from_macros_not_from_ai_field(self, gpt_parser):
        """Calories must come from macro arithmetic, never from AI-provided kcal field."""
        gpt_response = {
            "structured_data": {
                "dish_name": "Test Meal",
                "foods": [
                    {
                        "name": "Rice",
                        "quantity_g": 100,
                        "macros": {
                            "protein_g": 3.0,
                            "carbs_g": 28.0,
                            "fat_g": 0.5,
                        },
                    }
                ],
                "confidence": 0.9,
            }
        }

        nutrition = gpt_parser.parse_to_nutrition(gpt_response)
        # Calories = protein*4 + carbs*4 + fat*9 = 3*4 + 28*4 + 0.5*9 = 12 + 112 + 4.5 = 128.5
        assert nutrition.calories == pytest.approx(128.5, abs=1.0)
        assert nutrition.calories < 500


def test_parse_food_label_to_nutrition_logs_one_serving(gpt_parser):
    gpt_response = {
        "structured_data": {
            "product_name": "Cereal",
            "brand": "Acme",
            "serving_size": {"display_text": "2/3 cup (55g)", "grams": 55},
            "servings_per_package": 8,
            "label_calories_per_serving": 230,
            "macros_per_serving": {
                "protein_g": 3,
                "carbs_g": 37,
                "fat_g": 8,
                "fiber_g": 4,
                "sugar_g": 12,
            },
            "confidence": 0.91,
        }
    }

    nutrition = gpt_parser.parse_food_label_to_nutrition(gpt_response)

    assert nutrition.food_items[0].name == "Cereal"
    assert nutrition.food_items[0].quantity == pytest.approx(55)
    assert nutrition.food_items[0].unit == "g"
    assert nutrition.calories == pytest.approx(224)


def test_parse_food_label_metadata_returns_validated_label_data(gpt_parser):
    gpt_response = {
        "structured_data": {
            "product_name": "Cereal",
            "serving_size": {"display_text": "55g", "grams": 55},
            "servings_per_package": 8,
            "macros_per_serving": {"protein_g": 3, "carbs_g": 37, "fat_g": 8},
            "confidence": 0.91,
        }
    }

    metadata = gpt_parser.parse_food_label_metadata(gpt_response)

    assert metadata["product_name"] == "Cereal"
    assert metadata["serving_size"]["grams"] == pytest.approx(55)
    assert metadata["servings_per_package"] == pytest.approx(8)
