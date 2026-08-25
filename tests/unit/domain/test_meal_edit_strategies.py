"""
Unit tests for meal edit strategies.

Tests cover the strategy pattern implementation for add, update, and remove operations
on food items during meal editing.
"""

import uuid
from unittest.mock import AsyncMock, Mock

import pytest

from src.app.commands.meal.edit_meal_command import (
    CustomNutritionData,
    FoodItemChange,
    NutritionOverride,
)
from src.domain.model import FoodItem, Macros
from src.domain.ports.food_reference_repository_port import (
    FoodReferenceNutritionProjection,
    FoodReferenceServingProjection,
)
from src.domain.strategies.meal_edit_strategies import (
    AddFoodItemStrategy,
    FoodItemChangeStrategyFactory,
    RemoveFoodItemStrategy,
    UpdateFoodItemStrategy,
)


class TestRemoveFoodItemStrategy:
    """Tests for RemoveFoodItemStrategy."""

    @pytest.mark.asyncio
    async def test_remove_existing_food_item(self):
        """Test successfully removing an existing food item."""
        # Arrange
        mock_nutrition_service = Mock()
        strategy = RemoveFoodItemStrategy(mock_nutrition_service)

        food_items_dict = {
            "item1": FoodItem(
                id="item1",
                name="Chicken",
                quantity=100.0,
                unit="g",
                macros=Macros(protein=30.0, carbs=0.0, fat=8.0),
            ),
            "item2": FoodItem(
                id="item2",
                name="Rice",
                quantity=150.0,
                unit="g",
                macros=Macros(protein=4.0, carbs=40.0, fat=1.0),
            ),
        }

        change = FoodItemChange(action="remove", id="item1")

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert
        assert "item1" not in food_items_dict
        assert "item2" in food_items_dict
        assert len(food_items_dict) == 1

    @pytest.mark.asyncio
    async def test_remove_nonexistent_food_item(self):
        """Test removing a non-existent food item doesn't raise error."""
        # Arrange
        mock_nutrition_service = Mock()
        strategy = RemoveFoodItemStrategy(mock_nutrition_service)

        food_items_dict = {
            "item1": FoodItem(
                id="item1",
                name="Chicken",
                quantity=100.0,
                unit="g",
                macros=Macros(protein=30.0, carbs=0.0, fat=8.0),
            )
        }

        change = FoodItemChange(action="remove", id="nonexistent")

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert
        assert "item1" in food_items_dict
        assert len(food_items_dict) == 1

    @pytest.mark.asyncio
    async def test_remove_without_id(self):
        """Test remove action without id is handled gracefully."""
        # Arrange
        mock_nutrition_service = Mock()
        strategy = RemoveFoodItemStrategy(mock_nutrition_service)

        food_items_dict = {
            "item1": FoodItem(
                id="item1",
                name="Chicken",
                quantity=100.0,
                unit="g",
                macros=Macros(protein=30.0, carbs=0.0, fat=8.0),
            )
        }

        change = FoodItemChange(action="remove", id=None)

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert - Nothing should be removed
        assert len(food_items_dict) == 1
        assert "item1" in food_items_dict

    @pytest.mark.asyncio
    async def test_remove_from_empty_dict(self):
        """Test removing from empty dictionary doesn't raise error."""
        # Arrange
        mock_nutrition_service = Mock()
        strategy = RemoveFoodItemStrategy(mock_nutrition_service)

        food_items_dict = {}
        change = FoodItemChange(action="remove", id="item1")

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert
        assert len(food_items_dict) == 0


class TestUpdateFoodItemStrategy:
    """Tests for UpdateFoodItemStrategy."""

    @pytest.mark.asyncio
    async def test_update_quantity_same_unit(self):
        """Test updating quantity with same unit scales nutrition proportionally."""
        # Arrange
        mock_nutrition_service = Mock()
        strategy = UpdateFoodItemStrategy(mock_nutrition_service)

        food_items_dict = {
            "item1": FoodItem(
                id="item1",
                name="Chicken",
                quantity=100.0,
                unit="g",
                macros=Macros(protein=30.0, carbs=0.0, fat=8.0),
                confidence=0.9,
                fdc_id=123,
                food_reference_id=1001,
                is_custom=False,
            )
        }

        # Double the quantity
        change = FoodItemChange(action="update", id="item1", quantity=200.0)

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert
        updated_item = food_items_dict["item1"]
        assert updated_item.quantity == 200.0
        assert updated_item.unit == "g"
        # calories derived from doubled macros: 60*4 + 0*4 + 16*9 = 384
        assert updated_item.calories == pytest.approx(384.0)
        assert updated_item.macros.protein == 60.0  # Doubled
        assert updated_item.macros.fat == 16.0  # Doubled
        assert updated_item.food_reference_id == 1001

    @pytest.mark.asyncio
    async def test_update_keeps_manual_values_independent_from_source_macros(self):
        strategy = UpdateFoodItemStrategy(Mock())
        food_items_dict = {
            "item1": FoodItem(
                id="item1",
                name="Chicken",
                quantity=100.0,
                unit="g",
                macros=Macros(protein=30.0, carbs=0.0, fat=8.0),
            )
        }

        await strategy.apply(
            food_items_dict,
            FoodItemChange(
                action="update",
                id="item1",
                nutrition_override=NutritionOverride(
                    calories=123.4,
                    protein=-5.0,
                    carbs=999.0,
                    fat=1.0,
                ),
            ),
        )

        updated_item = food_items_dict["item1"]
        assert updated_item.macros.protein == 30.0
        assert updated_item.calories == 123.4
        assert updated_item.effective_macros.protein == -5.0
        assert updated_item.effective_macros.carbs == 999.0

    @pytest.mark.asyncio
    async def test_update_unit_with_nutrition_service_success(self):
        """Test updating unit fetches new nutrition from service."""
        # Arrange
        mock_nutrition_service = Mock()
        mock_scaled_nutrition = Mock()
        mock_scaled_nutrition.calories = 250.0
        mock_scaled_nutrition.protein = 35.0
        mock_scaled_nutrition.carbs = 0.0
        mock_scaled_nutrition.fat = 10.0

        mock_nutrition_service.get_nutrition_for_ingredient.return_value = (
            mock_scaled_nutrition
        )

        strategy = UpdateFoodItemStrategy(mock_nutrition_service)

        food_items_dict = {
            "item1": FoodItem(
                id="item1",
                name="Chicken",
                quantity=100.0,
                unit="g",
                macros=Macros(protein=30.0, carbs=0.0, fat=8.0),
                fdc_id=123,
                food_reference_id=1002,
                is_custom=False,
            )
        }

        # Change unit from grams to ounces
        change = FoodItemChange(action="update", id="item1", quantity=150.0, unit="oz")

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert
        updated_item = food_items_dict["item1"]
        assert updated_item.quantity == 150.0
        assert updated_item.unit == "oz"
        # calories derived: 35*4 + 0*4 + 10*9 = 230
        assert updated_item.calories == pytest.approx(230.0)
        assert updated_item.macros.protein == 35.0
        assert updated_item.food_reference_id == 1002

        # Verify nutrition service was called
        mock_nutrition_service.get_nutrition_for_ingredient.assert_called_once_with(
            name="Chicken", quantity=150.0, unit="oz", fdc_id=123
        )

    @pytest.mark.asyncio
    async def test_update_unit_uses_canonical_fatsecret_reference(self):
        mock_nutrition_service = Mock()
        mock_food_references = Mock()
        mock_food_references.get_nutrition_projection = AsyncMock(
            return_value=FoodReferenceNutritionProjection(
                id=1002,
                name="Pâté",
                source="fatsecret",
                is_verified=True,
                protein_100g=3.0,
                carbs_100g=1.0,
                fat_100g=5.0,
                servings=[
                    FoodReferenceServingProjection(
                        name="Muỗng Canh",
                        grams=15.0,
                        milliliters=None,
                    ),
                ],
            )
        )
        strategy = UpdateFoodItemStrategy(
            mock_nutrition_service,
            mock_food_references,
        )
        food_items_dict = {
            "item1": FoodItem(
                id="item1",
                name="Pâté",
                quantity=1.0,
                unit="Muỗng Canh",
                macros=Macros(protein=3.0, carbs=1.0, fat=5.0),
                food_reference_id=1002,
                is_custom=True,
            )
        }

        await strategy.apply(
            food_items_dict,
            FoodItemChange(action="update", id="item1", quantity=1.0, unit="kg"),
        )

        updated_item = food_items_dict["item1"]
        assert updated_item.quantity == 1.0
        assert updated_item.unit == "kg"
        assert updated_item.macros.protein == pytest.approx(30.0)
        assert updated_item.macros.carbs == pytest.approx(10.0)
        assert updated_item.macros.fat == pytest.approx(50.0)
        mock_food_references.get_nutrition_projection.assert_awaited_once_with(1002)
        mock_nutrition_service.get_nutrition_for_ingredient.assert_not_called()

    @pytest.mark.asyncio
    async def test_v2_update_unit_uses_snapshot_serving_weight(self):
        strategy = UpdateFoodItemStrategy(Mock())
        snapshot = {
            "protein_per_100g": 2.7,
            "carbs_per_100g": 28.0,
            "fat_per_100g": 0.3,
            "fiber_per_100g": 0.4,
            "sugar_per_100g": 0.1,
            "allowed_units": [
                {"unit": "g", "gram_weight": 1.0},
                {"unit": "cup", "gram_weight": 158.0},
            ],
        }
        food_items_dict = {
            "item-1": FoodItem(
                id="item-1",
                name="Rice",
                quantity=100.0,
                unit="g",
                macros=Macros(protein=2.7, carbs=28.0, fat=0.3),
                nutrition_contract_version="2",
                source_snapshot=snapshot,
                allowed_units=snapshot["allowed_units"],
            )
        }

        await strategy.apply(
            food_items_dict,
            FoodItemChange(action="update", id="item-1", quantity=1, unit="cup"),
        )

        assert food_items_dict["item-1"].macros.protein == pytest.approx(4.27)

    @pytest.mark.asyncio
    async def test_update_custom_nutrition_preserves_food_reference_id(self):
        """Custom nutrition updates preserve canonical food reference identity."""
        mock_nutrition_service = Mock()
        strategy = UpdateFoodItemStrategy(mock_nutrition_service)

        food_items_dict = {
            "item1": FoodItem(
                id="item1",
                name="Chicken",
                quantity=100.0,
                unit="g",
                macros=Macros(protein=30.0, carbs=0.0, fat=8.0),
                fdc_id=123,
                food_reference_id=1003,
                is_custom=False,
            )
        }
        change = FoodItemChange(
            action="update",
            id="item1",
            quantity=120.0,
            custom_nutrition=CustomNutritionData(
                calories_per_100g=165.0,
                protein_per_100g=31.0,
                carbs_per_100g=0.0,
                fat_per_100g=3.6,
            ),
        )

        await strategy.apply(food_items_dict, change)

        updated_item = food_items_dict["item1"]
        assert updated_item.quantity == 120.0
        assert updated_item.food_reference_id == 1003
        assert updated_item.is_custom is True

    @pytest.mark.asyncio
    async def test_update_unit_nutrition_service_fails_preserves_source(self):
        """Keep source nutrition unchanged when canonical data is unavailable."""
        # Arrange
        mock_nutrition_service = Mock()
        mock_nutrition_service.get_nutrition_for_ingredient.return_value = (
            None  # Service fails
        )

        strategy = UpdateFoodItemStrategy(mock_nutrition_service)

        food_items_dict = {
            "item1": FoodItem(
                id="item1",
                name="Chicken",
                quantity=100.0,
                unit="g",
                macros=Macros(protein=30.0, carbs=0.0, fat=8.0),
                confidence=0.9,
                fdc_id=123,
                is_custom=False,
            )
        }

        # Change unit
        change = FoodItemChange(action="update", id="item1", quantity=150.0, unit="oz")

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert - Never synthesize a new unit's nutrition from the old unit.
        updated_item = food_items_dict["item1"]
        assert updated_item.quantity == 150.0
        assert updated_item.unit == "oz"
        assert updated_item.calories == pytest.approx(192.0)
        assert updated_item.macros.protein == pytest.approx(30.0)

    @pytest.mark.asyncio
    async def test_update_quantity_only(self):
        """Test updating only quantity preserves unit."""
        # Arrange
        mock_nutrition_service = Mock()
        strategy = UpdateFoodItemStrategy(mock_nutrition_service)

        food_items_dict = {
            "item1": FoodItem(
                id="item1",
                name="Rice",
                quantity=100.0,
                unit="g",
                macros=Macros(protein=2.7, carbs=28.0, fat=0.3),
                confidence=0.9,
                fdc_id=456,
                is_custom=False,
            )
        }

        change = FoodItemChange(action="update", id="item1", quantity=75.0)

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert
        updated_item = food_items_dict["item1"]
        assert updated_item.quantity == 75.0
        assert updated_item.unit == "g"  # Preserved
        # Scaled by 0.75x (75/100), calories derived from macros
        # (2.7*0.75)*4 + (28*0.75)*4 + (0.3*0.75)*9 = 8.1+84+2.025 = 94.125
        assert updated_item.calories == pytest.approx(94.125, rel=0.01)
        assert updated_item.macros.carbs == pytest.approx(21.0, rel=0.01)

    @pytest.mark.asyncio
    async def test_update_invalid_id(self):
        """Test update with invalid id is handled gracefully."""
        # Arrange
        mock_nutrition_service = Mock()
        strategy = UpdateFoodItemStrategy(mock_nutrition_service)

        food_items_dict = {
            "item1": FoodItem(
                id="item1",
                name="Chicken",
                quantity=100.0,
                unit="g",
                macros=Macros(protein=30.0, carbs=0.0, fat=8.0),
            )
        }

        change = FoodItemChange(action="update", id="nonexistent", quantity=200.0)

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert - Original item unchanged
        assert food_items_dict["item1"].quantity == 100.0

    @pytest.mark.asyncio
    async def test_update_without_id(self):
        """Test update without id is handled gracefully."""
        # Arrange
        mock_nutrition_service = Mock()
        strategy = UpdateFoodItemStrategy(mock_nutrition_service)

        food_items_dict = {
            "item1": FoodItem(
                id="item1",
                name="Chicken",
                quantity=100.0,
                unit="g",
                macros=Macros(protein=30.0, carbs=0.0, fat=8.0),
            )
        }

        change = FoodItemChange(action="update", id=None, quantity=200.0)

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert - Original item unchanged
        assert food_items_dict["item1"].quantity == 100.0

    @pytest.mark.asyncio
    async def test_update_preserves_item_id(self):
        """Test update preserves the original item ID."""
        # Arrange
        mock_nutrition_service = Mock()
        strategy = UpdateFoodItemStrategy(mock_nutrition_service)

        original_id = "original_item_id"
        food_items_dict = {
            original_id: FoodItem(
                id=original_id,
                name="Chicken",
                quantity=100.0,
                unit="g",
                macros=Macros(protein=30.0, carbs=0.0, fat=8.0),
                fdc_id=123,
                is_custom=False,
            )
        }

        change = FoodItemChange(action="update", id=original_id, quantity=150.0)

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert
        assert original_id in food_items_dict
        assert food_items_dict[original_id].id == original_id


class TestAddFoodItemStrategy:
    """Tests for AddFoodItemStrategy."""

    @pytest.mark.asyncio
    async def test_add_with_custom_nutrition(self):
        """Test adding food item with custom nutrition data."""
        # Arrange
        mock_nutrition_service = Mock()
        strategy = AddFoodItemStrategy(mock_nutrition_service, food_service=None)

        food_items_dict = {}

        custom_nutrition = CustomNutritionData(
            calories_per_100g=250.0,
            protein_per_100g=20.0,
            carbs_per_100g=30.0,
            fat_per_100g=10.0,
        )

        change = FoodItemChange(
            action="add",
            name="Custom Food",
            quantity=150.0,
            unit="g",
            custom_nutrition=custom_nutrition,
        )

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert
        assert len(food_items_dict) == 1
        added_item = list(food_items_dict.values())[0]
        assert added_item.name == "Custom Food"
        assert added_item.quantity == 150.0
        assert added_item.unit == "g"
        # Scaled by 1.5x (150/100), calories derived: 30*4+45*4+15*9 = 435
        assert added_item.calories == pytest.approx(435.0)
        assert added_item.macros.protein == 30.0
        assert added_item.macros.carbs == 45.0
        assert added_item.macros.fat == 15.0
        assert added_item.is_custom is True

    @pytest.mark.asyncio
    async def test_add_with_nutrition_service(self):
        """Test adding food item using nutrition service."""
        # Arrange
        mock_nutrition_service = Mock()
        mock_scaled_nutrition = Mock()
        mock_scaled_nutrition.calories = 200.0
        mock_scaled_nutrition.protein = 25.0
        mock_scaled_nutrition.carbs = 0.0
        mock_scaled_nutrition.fat = 8.0

        mock_nutrition_service.get_nutrition_for_ingredient.return_value = (
            mock_scaled_nutrition
        )

        strategy = AddFoodItemStrategy(mock_nutrition_service, food_service=None)

        food_items_dict = {}

        change = FoodItemChange(
            action="add", name="Chicken Breast", quantity=100.0, unit="g", fdc_id=171077
        )

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert
        assert len(food_items_dict) == 1
        added_item = list(food_items_dict.values())[0]
        assert added_item.name == "Chicken Breast"
        assert added_item.quantity == 100.0
        assert added_item.unit == "g"
        # calories derived: 25*4 + 0*4 + 8*9 = 172
        assert added_item.calories == pytest.approx(172.0)
        assert added_item.macros.protein == 25.0
        assert added_item.fdc_id == 171077
        assert added_item.is_custom is False

        # Verify service was called
        mock_nutrition_service.get_nutrition_for_ingredient.assert_called_once_with(
            name="Chicken Breast", quantity=100.0, unit="g", fdc_id=171077
        )

    @pytest.mark.asyncio
    async def test_add_without_nutrition_data(self):
        """Test adding food item when no nutrition data available."""
        # Arrange
        mock_nutrition_service = Mock()
        mock_nutrition_service.get_nutrition_for_ingredient.return_value = None

        strategy = AddFoodItemStrategy(mock_nutrition_service, food_service=None)

        food_items_dict = {}

        change = FoodItemChange(
            action="add", name="Unknown Food", quantity=100.0, unit="g"
        )

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert - Item added with zero macros as fallback (never silently discard)
        assert len(food_items_dict) == 1
        added_item = list(food_items_dict.values())[0]
        assert added_item.name == "Unknown Food"
        assert added_item.quantity == 100.0
        assert added_item.unit == "g"
        assert added_item.macros.protein == 0
        assert added_item.macros.carbs == 0
        assert added_item.macros.fat == 0
        assert added_item.confidence == 0.3
        assert added_item.is_custom is True

    @pytest.mark.asyncio
    async def test_add_uses_default_quantity_and_unit(self):
        """Test adding food item uses defaults when not provided."""
        # Arrange
        mock_nutrition_service = Mock()
        mock_scaled_nutrition = Mock()
        mock_scaled_nutrition.calories = 200.0
        mock_scaled_nutrition.protein = 25.0
        mock_scaled_nutrition.carbs = 0.0
        mock_scaled_nutrition.fat = 8.0

        mock_nutrition_service.get_nutrition_for_ingredient.return_value = (
            mock_scaled_nutrition
        )

        strategy = AddFoodItemStrategy(mock_nutrition_service, food_service=None)

        food_items_dict = {}

        # No quantity or unit provided
        change = FoodItemChange(action="add", name="Chicken Breast")

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert
        assert len(food_items_dict) == 1
        added_item = list(food_items_dict.values())[0]
        assert added_item.quantity == 100  # Default
        assert added_item.unit == "g"  # Default

        # Verify service was called with defaults
        mock_nutrition_service.get_nutrition_for_ingredient.assert_called_once_with(
            name="Chicken Breast", quantity=100, unit="g", fdc_id=None
        )

    @pytest.mark.asyncio
    async def test_add_generates_unique_id(self):
        """Test that each added item gets a unique ID."""
        # Arrange
        mock_nutrition_service = Mock()
        mock_scaled_nutrition = Mock()
        mock_scaled_nutrition.calories = 200.0
        mock_scaled_nutrition.protein = 25.0
        mock_scaled_nutrition.carbs = 0.0
        mock_scaled_nutrition.fat = 8.0

        mock_nutrition_service.get_nutrition_for_ingredient.return_value = (
            mock_scaled_nutrition
        )

        strategy = AddFoodItemStrategy(mock_nutrition_service, food_service=None)

        food_items_dict = {}

        # Act - Add two items
        change1 = FoodItemChange(action="add", name="Food 1")
        change2 = FoodItemChange(action="add", name="Food 2")

        await strategy.apply(food_items_dict, change1)
        await strategy.apply(food_items_dict, change2)

        # Assert
        assert len(food_items_dict) == 2
        ids = list(food_items_dict.keys())
        assert ids[0] != ids[1]  # Different IDs
        # Verify they are valid UUIDs
        try:
            uuid.UUID(ids[0])
            uuid.UUID(ids[1])
        except ValueError:
            pytest.fail("Generated IDs are not valid UUIDs")

    @pytest.mark.asyncio
    async def test_add_custom_nutrition_with_different_quantity(self):
        """Test custom nutrition scaling with various quantities."""
        # Arrange
        mock_nutrition_service = Mock()
        strategy = AddFoodItemStrategy(mock_nutrition_service, food_service=None)

        custom_nutrition = CustomNutritionData(
            calories_per_100g=100.0,
            protein_per_100g=10.0,
            carbs_per_100g=20.0,
            fat_per_100g=5.0,
            fiber_per_100g=4.0,
            sugar_per_100g=8.0,
        )

        test_cases = [
            (50.0, 0.5),  # Half
            (100.0, 1.0),  # Same
            (200.0, 2.0),  # Double
        ]

        for quantity, scale in test_cases:
            food_items_dict = {}
            change = FoodItemChange(
                action="add",
                name="Test Food",
                quantity=quantity,
                unit="g",
                custom_nutrition=custom_nutrition,
            )

            # Act
            await strategy.apply(food_items_dict, change)

            # Assert
            added_item = list(food_items_dict.values())[0]
            # calories derived: protein + net carbs + fiber + fat = 157*scale
            assert added_item.calories == pytest.approx(157.0 * scale, rel=0.01)
            assert added_item.macros.protein == pytest.approx(10.0 * scale, rel=0.01)
            assert added_item.macros.carbs == pytest.approx(20.0 * scale, rel=0.01)
            assert added_item.macros.fat == pytest.approx(5.0 * scale, rel=0.01)
            assert added_item.macros.fiber == pytest.approx(4.0 * scale, rel=0.01)
            assert added_item.macros.sugar == pytest.approx(8.0 * scale, rel=0.01)

    @pytest.mark.asyncio
    async def test_add_priority_custom_nutrition_over_service(self):
        """Test that custom nutrition takes priority over nutrition service."""
        # Arrange
        mock_nutrition_service = Mock()
        mock_scaled_nutrition = Mock()
        mock_scaled_nutrition.calories = 999.0  # Should not be used

        mock_nutrition_service.get_nutrition_for_ingredient.return_value = (
            mock_scaled_nutrition
        )

        strategy = AddFoodItemStrategy(mock_nutrition_service, food_service=None)

        food_items_dict = {}

        custom_nutrition = CustomNutritionData(
            calories_per_100g=250.0,
            protein_per_100g=20.0,
            carbs_per_100g=30.0,
            fat_per_100g=10.0,
        )

        change = FoodItemChange(
            action="add",
            name="Food",
            quantity=100.0,
            unit="g",
            custom_nutrition=custom_nutrition,
        )

        # Act
        await strategy.apply(food_items_dict, change)

        # Assert
        added_item = list(food_items_dict.values())[0]
        # calories derived from custom macros: 20*4+30*4+10*9 = 290 (not stored 250)
        assert added_item.calories == pytest.approx(
            290.0
        )  # From custom macros, not service

        # Verify service was NOT called
        mock_nutrition_service.get_nutrition_for_ingredient.assert_not_called()


class TestFoodItemChangeStrategyFactory:
    """Tests for FoodItemChangeStrategyFactory."""

    def test_create_strategies_returns_all_strategies(self):
        """Test factory creates all three strategies."""
        # Arrange
        mock_nutrition_service = Mock()
        mock_food_service = Mock()

        # Act
        strategies = FoodItemChangeStrategyFactory.create_strategies(
            mock_nutrition_service, mock_food_service
        )

        # Assert
        assert "add" in strategies
        assert "update" in strategies
        assert "remove" in strategies
        assert len(strategies) == 3

    def test_create_strategies_correct_types(self):
        """Test factory creates correct strategy types."""
        # Arrange
        mock_nutrition_service = Mock()

        # Act
        strategies = FoodItemChangeStrategyFactory.create_strategies(
            mock_nutrition_service
        )

        # Assert
        assert isinstance(strategies["add"], AddFoodItemStrategy)
        assert isinstance(strategies["update"], UpdateFoodItemStrategy)
        assert isinstance(strategies["remove"], RemoveFoodItemStrategy)

    def test_create_strategies_passes_services(self):
        """Test factory passes services to strategies correctly."""
        # Arrange
        mock_nutrition_service = Mock()
        mock_food_service = Mock()

        # Act
        strategies = FoodItemChangeStrategyFactory.create_strategies(
            mock_nutrition_service, mock_food_service
        )

        # Assert
        assert strategies["add"].nutrition_service == mock_nutrition_service
        assert strategies["add"].food_service == mock_food_service
        assert strategies["update"].nutrition_service == mock_nutrition_service
        assert strategies["remove"].nutrition_service == mock_nutrition_service

    def test_create_strategies_without_food_service(self):
        """Test factory works without food service."""
        # Arrange
        mock_nutrition_service = Mock()

        # Act
        strategies = FoodItemChangeStrategyFactory.create_strategies(
            mock_nutrition_service, food_service=None
        )

        # Assert
        assert strategies["add"].food_service is None
        # Other strategies don't use food_service


class TestStrategiesIntegration:
    """Integration tests for strategies working together."""

    @pytest.mark.asyncio
    async def test_add_update_remove_workflow(self):
        """Test complete workflow of adding, updating, and removing items."""
        # Arrange
        mock_nutrition_service = Mock()
        mock_scaled_nutrition = Mock()
        mock_scaled_nutrition.calories = 200.0
        mock_scaled_nutrition.protein = 25.0
        mock_scaled_nutrition.carbs = 0.0
        mock_scaled_nutrition.fat = 8.0

        mock_nutrition_service.get_nutrition_for_ingredient.return_value = (
            mock_scaled_nutrition
        )

        strategies = FoodItemChangeStrategyFactory.create_strategies(
            mock_nutrition_service
        )

        food_items_dict = {}

        # Act 1: Add item
        add_change = FoodItemChange(
            action="add", name="Chicken", quantity=100.0, unit="g"
        )
        await strategies["add"].apply(food_items_dict, add_change)

        assert len(food_items_dict) == 1
        item_id = list(food_items_dict.keys())[0]

        # Act 2: Update item
        update_change = FoodItemChange(action="update", id=item_id, quantity=150.0)
        await strategies["update"].apply(food_items_dict, update_change)

        assert food_items_dict[item_id].quantity == 150.0

        # Act 3: Remove item
        remove_change = FoodItemChange(action="remove", id=item_id)
        await strategies["remove"].apply(food_items_dict, remove_change)

        # Assert
        assert len(food_items_dict) == 0

    @pytest.mark.asyncio
    async def test_multiple_adds(self):
        """Test adding multiple items in sequence."""
        # Arrange
        mock_nutrition_service = Mock()
        mock_scaled_nutrition = Mock()
        mock_scaled_nutrition.calories = 200.0
        mock_scaled_nutrition.protein = 25.0
        mock_scaled_nutrition.carbs = 0.0
        mock_scaled_nutrition.fat = 8.0

        mock_nutrition_service.get_nutrition_for_ingredient.return_value = (
            mock_scaled_nutrition
        )

        add_strategy = AddFoodItemStrategy(mock_nutrition_service)

        food_items_dict = {}

        # Act - Add multiple items
        items_to_add = ["Chicken", "Rice", "Vegetables"]
        for item_name in items_to_add:
            change = FoodItemChange(
                action="add", name=item_name, quantity=100.0, unit="g"
            )
            await add_strategy.apply(food_items_dict, change)

        # Assert
        assert len(food_items_dict) == 3
        item_names = [item.name for item in food_items_dict.values()]
        assert "Chicken" in item_names
        assert "Rice" in item_names
        assert "Vegetables" in item_names
