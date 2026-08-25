"""
Unit tests for meal edit request validation.
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from src.api.schemas.request.meal_requests import (
    AddCustomIngredientRequest,
    CreateManualMealFromFoodsRequest,
    CustomNutritionRequest,
    EditMealIngredientsRequest,
    FoodItemChangeRequest,
    NutritionOverrideRequest,
)


@pytest.mark.unit
class TestFoodItemChangeRequest:
    """Test FoodItemChangeRequest validation."""

    def test_valid_add_request(self):
        """Test valid add request."""
        # Arrange & Act
        request = FoodItemChangeRequest(
            action="add",
            name="Test Food",
            quantity=100.0,
            unit="g",
            custom_nutrition=CustomNutritionRequest(
                protein_per_100g=10.0,
                carbs_per_100g=20.0,
                fat_per_100g=8.0,
            ),
        )

        # Assert
        assert request.action == "add"
        assert request.name == "Test Food"
        assert request.quantity == 100.0
        assert request.unit == "g"
        # Derived: 10*4 + 20*4 + 8*9 = 192
        assert request.custom_nutrition.calories_per_100g == 192.0

    def test_valid_add_request_with_allowed_units(self):
        """Test preserving food-specific unit options."""
        request = FoodItemChangeRequest(
            action="add",
            name="Chicken Breast",
            quantity=100.0,
            unit="g",
            allowed_units=[
                {
                    "unit": "g",
                    "gram_weight": 100.0,
                    "description": "100 g",
                },
                {
                    "unit": "small breast (yield after cooking, bone removed)",
                    "gram_weight": 84.0,
                    "description": "1/2 small (yield after cooking, bone removed)",
                },
                {
                    "unit": "cup, cooked, diced",
                    "gram_weight": 135.0,
                    "description": "1 cup cooked, diced",
                },
            ],
        )

        assert len(request.allowed_units) == 3
        assert request.allowed_units[0].unit == "g"
        assert request.allowed_units[0].description == "100 g"
        assert request.allowed_units[1].unit.startswith("small breast")
        assert request.allowed_units[2].unit == "cup, cooked, diced"

    def test_valid_update_request(self):
        """Test valid update request."""
        # Arrange & Act
        request = FoodItemChangeRequest(
            action="update", id="test-food-item-id", quantity=150.0, unit="g"
        )

        # Assert
        assert request.action == "update"
        assert request.id == "test-food-item-id"
        assert request.quantity == 150.0
        assert request.unit == "g"

    def test_valid_remove_request(self):
        """Test valid remove request."""
        # Arrange & Act
        request = FoodItemChangeRequest(action="remove", id="test-food-item-id")

        # Assert
        assert request.action == "remove"
        assert request.id == "test-food-item-id"

    def test_manual_nutrition_override_allows_independent_values(self):
        request = FoodItemChangeRequest(
            action="update",
            id="test-food-item-id",
            nutrition_override=NutritionOverrideRequest(
                calories=123.4,
                protein=5.0,
                carbs=999.0,
                fat=1.0,
            ),
        )

        assert request.nutrition_override.calories == 123.4
        assert request.nutrition_override.protein == 5.0

    def test_invalid_action(self):
        """Test invalid action value."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            FoodItemChangeRequest(
                action="invalid_action", name="Test Food", quantity=100.0, unit="g"
            )

    def test_negative_quantity(self):
        """Test negative quantity validation."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            FoodItemChangeRequest(
                action="add", name="Test Food", quantity=-50.0, unit="g"
            )

    def test_zero_quantity(self):
        """Test zero quantity validation."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            FoodItemChangeRequest(
                action="add", name="Test Food", quantity=0.0, unit="g"
            )

    def test_quantity_too_large(self):
        """Test quantity too large validation."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            FoodItemChangeRequest(
                action="add",
                name="Test Food",
                quantity=15000.0,  # Over 10000 limit
                unit="g",
            )

    def test_empty_name(self):
        """Test empty name validation."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            FoodItemChangeRequest(action="add", name="", quantity=100.0, unit="g")

    def test_name_too_long(self):
        """Test name too long validation."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            FoodItemChangeRequest(
                action="add",
                name="a" * 201,  # Over 200 character limit
                quantity=100.0,
                unit="g",
            )

    def test_empty_unit(self):
        """Test empty unit validation."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            FoodItemChangeRequest(
                action="add", name="Test Food", quantity=100.0, unit=""
            )

    def test_unit_too_long(self):
        """Test unit too long validation."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            FoodItemChangeRequest(
                action="add",
                name="Test Food",
                quantity=100.0,
                unit="a" * 121,  # Over 120 character limit
            )


@pytest.mark.unit
class TestCustomNutritionRequest:
    """Test CustomNutritionRequest validation."""

    def test_valid_nutrition_request(self):
        """Test valid nutrition request with derived calories."""
        # Arrange & Act
        request = CustomNutritionRequest(
            protein_per_100g=15.0,
            carbs_per_100g=25.0,
            fat_per_100g=8.0,
        )

        # Assert — calories derived: 15*4 + 25*4 + 8*9 = 232
        assert request.calories_per_100g == 232.0
        assert request.protein_per_100g == 15.0
        assert request.carbs_per_100g == 25.0
        assert request.fat_per_100g == 8.0

    def test_negative_protein_is_rejected(self):
        with pytest.raises(ValidationError):
            CustomNutritionRequest(
                protein_per_100g=-5.0, carbs_per_100g=25.0, fat_per_100g=8.0
            )

    def test_protein_above_density_limit_is_user_editable(self):
        request = CustomNutritionRequest(
            protein_per_100g=150.0,
            carbs_per_100g=25.0,
            fat_per_100g=8.0,
        )

        assert request.protein_per_100g == 150.0


@pytest.mark.unit
class TestCreateManualMealFromFoodsRequest:
    """Test manual meal creation request validation."""

    def test_prompt_source_allows_legacy_back_calculated_macro_rates(self):
        request = CreateManualMealFromFoodsRequest(
            dish_name="Pho bowl",
            source="prompt",
            items=[
                {
                    "name": "Pho bowl",
                    "quantity": 1.0,
                    "unit": "one very full noodle bowl",
                    "custom_nutrition": {
                        "protein_per_100g": 3000.0,
                        "carbs_per_100g": 8000.0,
                        "fat_per_100g": 1200.0,
                    },
                }
            ],
        )

        nutrition = request.items[0].custom_nutrition
        assert nutrition is not None
        assert nutrition.protein_per_100g == 3000.0

    def test_missing_source_allows_legacy_non_gram_prompt_payload(self):
        request = CreateManualMealFromFoodsRequest(
            dish_name="Pho bowl",
            items=[
                {
                    "name": "Pho bowl",
                    "quantity": 1.0,
                    "unit": "one very full noodle bowl",
                    "custom_nutrition": {
                        "protein_per_100g": 3000.0,
                        "carbs_per_100g": 8000.0,
                        "fat_per_100g": 1200.0,
                    },
                }
            ],
        )

        assert request.source is None

    def test_manual_source_allows_legacy_non_gram_prompt_payload(self):
        request = CreateManualMealFromFoodsRequest(
            dish_name="gà sốt cam",
            source="manual",
            items=[
                {
                    "name": "gà sốt cam",
                    "quantity": 1.0,
                    "unit": "đĩa gà sốt cam",
                    "custom_nutrition": {
                        "protein_per_100g": 3000.0,
                        "carbs_per_100g": 5000.0,
                        "fat_per_100g": 2000.0,
                    },
                }
            ],
        )

        assert request.source == "manual"

    def test_prompt_source_allows_legacy_back_calculated_fiber_and_sugar_rates(self):
        request = CreateManualMealFromFoodsRequest(
            dish_name="Smoothie bowl",
            source="prompt",
            items=[
                {
                    "name": "Smoothie bowl",
                    "quantity": 1.0,
                    "unit": "bowl",
                    "custom_nutrition": {
                        "protein_per_100g": 120.0,
                        "carbs_per_100g": 600.0,
                        "fat_per_100g": 80.0,
                        "fiber_per_100g": 150.0,
                        "sugar_per_100g": 240.0,
                    },
                }
            ],
        )

        nutrition = request.items[0].custom_nutrition
        assert nutrition is not None
        assert nutrition.fiber_per_100g == 150.0
        assert nutrition.sugar_per_100g == 240.0

    def test_missing_source_rejects_impossible_gram_macro_rates(self):
        with pytest.raises(ValidationError):
            CreateManualMealFromFoodsRequest(
                dish_name="Manual meal",
                items=[
                    {
                        "name": "Manual meal",
                        "quantity": 1.0,
                        "unit": "g",
                        "custom_nutrition": {
                            "protein_per_100g": 3000.0,
                            "carbs_per_100g": 10.0,
                            "fat_per_100g": 5.0,
                        },
                    }
                ],
            )

    def test_non_prompt_source_rejects_impossible_macro_rates(self):
        with pytest.raises(ValidationError):
            CreateManualMealFromFoodsRequest(
                dish_name="Manual meal",
                source="manual",
                items=[
                    {
                        "name": "Manual meal",
                        "quantity": 1.0,
                        "unit": "g",
                        "custom_nutrition": {
                            "protein_per_100g": 3000.0,
                            "carbs_per_100g": 10.0,
                            "fat_per_100g": 5.0,
                        },
                    }
                ],
            )

    def test_gram_custom_nutrition_rejects_impossible_fiber_and_sugar_rates(self):
        with pytest.raises(ValidationError):
            CreateManualMealFromFoodsRequest(
                dish_name="Granola",
                source="manual",
                items=[
                    {
                        "name": "Granola",
                        "quantity": 100.0,
                        "unit": "g",
                        "custom_nutrition": {
                            "protein_per_100g": 10.0,
                            "carbs_per_100g": 60.0,
                            "fat_per_100g": 8.0,
                            "fiber_per_100g": 120.0,
                            "sugar_per_100g": 140.0,
                        },
                    }
                ],
            )


@pytest.mark.unit
class TestEditMealIngredientsRequest:
    """Test EditMealIngredientsRequest validation."""

    def test_valid_edit_request(self):
        """Test valid edit meal ingredients request."""
        # Arrange & Act
        request = EditMealIngredientsRequest(
            dish_name="Updated Meal Name",
            food_item_changes=[
                FoodItemChangeRequest(
                    action="add",
                    name="New Food",
                    quantity=100.0,
                    unit="g",
                    custom_nutrition=CustomNutritionRequest(
                        protein_per_100g=10.0, carbs_per_100g=20.0, fat_per_100g=8.0
                    ),
                )
            ],
        )

        # Assert
        assert request.dish_name == "Updated Meal Name"
        assert len(request.food_item_changes) == 1
        assert request.food_item_changes[0].action == "add"

    def test_accepts_meal_nutrition_override_without_food_changes(self):
        request = EditMealIngredientsRequest(
            nutrition_override=NutritionOverrideRequest(
                calories=777.0,
                protein=1.0,
                carbs=2.0,
                fat=3.0,
            )
        )

        assert request.nutrition_override.calories == 777.0

    def test_valid_edit_request_without_dish_name(self):
        """Test valid edit request without dish name."""
        # Arrange & Act
        request = EditMealIngredientsRequest(
            food_item_changes=[
                FoodItemChangeRequest(
                    action="update", id="test-id", quantity=150.0, unit="g"
                )
            ]
        )

        # Assert
        assert request.dish_name is None
        assert len(request.food_item_changes) == 1

    def test_metadata_only_edit_request(self):
        """Test meal metadata edits without ingredient changes."""
        created_at = datetime(2026, 6, 28, 8, 30)

        request = EditMealIngredientsRequest(
            dish_name="Updated Meal",
            created_at=created_at,
            meal_type="breakfast",
            food_item_changes=[],
        )

        assert request.dish_name == "Updated Meal"
        assert request.created_at == created_at
        assert request.meal_type == "breakfast"
        assert request.food_item_changes == []

    def test_empty_edit_request(self):
        """Test empty edit requests are rejected."""
        with pytest.raises(ValidationError):
            EditMealIngredientsRequest()

    def test_accepts_more_than_fifty_item_changes(self):
        request = EditMealIngredientsRequest(
            food_item_changes=[
                {"action": "remove", "id": str(index)} for index in range(51)
            ]
        )

        assert len(request.food_item_changes) == 51

    def test_dish_name_too_long(self):
        """Test dish name too long validation."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            EditMealIngredientsRequest(
                dish_name="a" * 201,  # Over 200 character limit
                food_item_changes=[
                    FoodItemChangeRequest(
                        action="add", name="Test Food", quantity=100.0, unit="g"
                    )
                ],
            )

    def test_empty_dish_name(self):
        """Test empty dish name is accepted for clearing a meal name."""
        request = EditMealIngredientsRequest(
            dish_name="",
            food_item_changes=[],
        )

        assert request.dish_name == ""
        assert request.food_item_changes == []


@pytest.mark.unit
class TestAddCustomIngredientRequest:
    """Test AddCustomIngredientRequest validation."""

    def test_valid_custom_ingredient_request(self):
        """Test valid custom ingredient request."""
        # Arrange & Act
        request = AddCustomIngredientRequest(
            name="Homemade Sauce",
            quantity=50.0,
            unit="ml",
            nutrition=CustomNutritionRequest(
                calories_per_100g=150.0,
                protein_per_100g=2.0,
                carbs_per_100g=10.0,
                fat_per_100g=12.0,
            ),
        )

        # Assert
        assert request.name == "Homemade Sauce"
        assert request.quantity == 50.0
        assert request.unit == "ml"
        # Derived: 2*4 + 10*4 + 12*9 = 156
        assert request.nutrition.calories_per_100g == 156.0

    def test_empty_name(self):
        """Test empty name validation."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            AddCustomIngredientRequest(
                name="",
                quantity=50.0,
                unit="ml",
                nutrition=CustomNutritionRequest(
                    protein_per_100g=2.0, carbs_per_100g=10.0, fat_per_100g=12.0
                ),
            )

    def test_name_too_long(self):
        """Test name too long validation."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            AddCustomIngredientRequest(
                name="a" * 201,  # Over 200 character limit
                quantity=50.0,
                unit="ml",
                nutrition=CustomNutritionRequest(
                    protein_per_100g=2.0, carbs_per_100g=10.0, fat_per_100g=12.0
                ),
            )

    def test_negative_quantity(self):
        """Test negative quantity validation."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            AddCustomIngredientRequest(
                name="Test Ingredient",
                quantity=-25.0,
                unit="ml",
                nutrition=CustomNutritionRequest(
                    protein_per_100g=2.0, carbs_per_100g=10.0, fat_per_100g=12.0
                ),
            )

    def test_quantity_too_large(self):
        """Test quantity too large validation."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            AddCustomIngredientRequest(
                name="Test Ingredient",
                quantity=15000.0,  # Over 10000 limit
                unit="ml",
                nutrition=CustomNutritionRequest(
                    protein_per_100g=2.0, carbs_per_100g=10.0, fat_per_100g=12.0
                ),
            )

    def test_empty_unit(self):
        """Test empty unit validation."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            AddCustomIngredientRequest(
                name="Test Ingredient",
                quantity=50.0,
                unit="",
                nutrition=CustomNutritionRequest(
                    protein_per_100g=2.0, carbs_per_100g=10.0, fat_per_100g=12.0
                ),
            )
