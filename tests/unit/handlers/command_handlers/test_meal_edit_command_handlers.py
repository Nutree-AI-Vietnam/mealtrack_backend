"""
Unit tests for meal edit command handlers.
"""

import pytest

from src.api.exceptions import ResourceNotFoundException, ValidationException
from src.app.commands.meal import (
    AddCustomIngredientCommand,
    CustomNutritionData,
    EditMealCommand,
    FoodItemChange,
)
from src.app.events.meal import MealEditedEvent
from src.app.handlers.command_handlers.edit_meal_command_handler import (
    EditMealCommandHandler,
)
from src.domain.model.meal import MealTranslation
from src.domain.model.nutrition import FoodItem, Macros
from src.infra.database.models import MealORM


@pytest.mark.unit
class TestEditMealCommandHandler:
    """Test EditMealCommand handler."""

    @pytest.mark.asyncio
    async def test_edit_meal_update_ingredient_quantity(
        self, event_bus, sample_meal_with_nutrition
    ):
        """Test updating ingredient quantity."""
        # Arrange
        meal = sample_meal_with_nutrition
        original_calories = meal.nutrition.calories

        # Get first food item ID
        first_food_item = meal.nutrition.food_items[0]
        id = first_food_item.id
        # Using the domain model's id field now

        command = EditMealCommand(
            meal_id=meal.meal_id,
            food_item_changes=[
                FoodItemChange(
                    action="update",
                    id=id,
                    quantity=200.0,  # Double the quantity
                    unit="g",
                )
            ],
        )

        # Act
        result = await event_bus.send(command)

        # Assert
        assert result["success"] is True
        assert result["meal_id"] == meal.meal_id
        assert result["edit_metadata"]["edit_count"] == 1
        assert result["edit_metadata"]["changes_summary"] == "Updated portion"

        # Check nutrition was recalculated
        updated_nutrition = result["updated_nutrition"]
        assert updated_nutrition["calories"] > original_calories

        # Check events
        assert len(result["events"]) == 1
        event = result["events"][0]
        assert isinstance(event, MealEditedEvent)
        assert event.meal_id == meal.meal_id
        assert event.edit_type == "ingredients_updated"

    @pytest.mark.asyncio
    async def test_edit_meal_add_custom_ingredient(
        self, event_bus, sample_meal_with_nutrition
    ):
        """Test adding a custom ingredient."""
        # Arrange
        meal = sample_meal_with_nutrition
        original_calories = meal.nutrition.calories

        command = EditMealCommand(
            meal_id=meal.meal_id,
            food_item_changes=[
                FoodItemChange(
                    action="add",
                    name="Homemade Sauce",
                    quantity=50.0,
                    unit="ml",
                    custom_nutrition=CustomNutritionData(
                        calories_per_100g=120.0,
                        protein_per_100g=2.0,
                        carbs_per_100g=8.0,
                        fat_per_100g=10.0,
                    ),
                )
            ],
        )

        # Act
        result = await event_bus.send(command)

        # Assert
        assert result["success"] is True
        assert result["edit_metadata"]["edit_count"] == 1
        assert "Added Homemade Sauce" in result["edit_metadata"]["changes_summary"]

        # Check nutrition increased
        updated_nutrition = result["updated_nutrition"]
        assert updated_nutrition["calories"] > original_calories

        # Check new food item was added
        updated_food_items = result["updated_food_items"]
        custom_item = next(
            (item for item in updated_food_items if item["name"] == "Homemade Sauce"),
            None,
        )
        assert custom_item is not None
        assert custom_item["is_custom"] is True
        assert custom_item["quantity"] == 50.0
        assert custom_item["calories"] == pytest.approx(
            65.0
        )  # derived: (2*4 + 8*4 + 10*9) * 0.5

    @pytest.mark.asyncio
    async def test_edit_meal_remove_ingredient(
        self, event_bus, sample_meal_with_nutrition
    ):
        """Test removing an ingredient."""
        # Arrange
        meal = sample_meal_with_nutrition
        original_calories = meal.nutrition.calories

        # Get first food item ID
        first_food_item = meal.nutrition.food_items[0]
        id = first_food_item.id
        # Using the domain model's id field now

        command = EditMealCommand(
            meal_id=meal.meal_id,
            food_item_changes=[FoodItemChange(action="remove", id=id)],
        )

        # Act
        result = await event_bus.send(command)

        # Assert
        assert result["success"] is True
        assert result["edit_metadata"]["changes_summary"] == "Removed ingredient"

        # Check nutrition decreased
        updated_nutrition = result["updated_nutrition"]
        assert updated_nutrition["calories"] < original_calories

        # Check food item was removed
        updated_food_items = result["updated_food_items"]
        assert len(updated_food_items) == len(meal.nutrition.food_items) - 1

    @pytest.mark.asyncio
    async def test_edit_meal_multiple_changes(
        self, event_bus, sample_meal_with_nutrition
    ):
        """Test multiple ingredient changes in one operation."""
        # Arrange
        meal = sample_meal_with_nutrition

        # Set up food item IDs using the domain model's id field
        food_item_1_id = meal.nutrition.food_items[0].id
        food_item_2_id = meal.nutrition.food_items[1].id

        command = EditMealCommand(
            meal_id=meal.meal_id,
            dish_name="Updated Meal Name",
            food_item_changes=[
                FoodItemChange(
                    action="update", id=food_item_1_id, quantity=150.0, unit="g"
                ),
                FoodItemChange(action="remove", id=food_item_2_id),
                FoodItemChange(
                    action="add",
                    name="New Ingredient",
                    quantity=100.0,
                    unit="g",
                    custom_nutrition=CustomNutritionData(
                        calories_per_100g=200.0,
                        protein_per_100g=5.0,
                        carbs_per_100g=15.0,
                        fat_per_100g=8.0,
                    ),
                ),
            ],
        )

        # Act
        result = await event_bus.send(command)

        # Assert
        assert result["success"] is True
        assert result["edit_metadata"]["edit_count"] == 1
        summary = result["edit_metadata"]["changes_summary"]
        assert "Updated portion" in summary
        assert "Removed ingredient" in summary
        assert "Added New Ingredient" in summary

    @pytest.mark.asyncio
    async def test_edit_meal_allows_clearing_dish_name(
        self, event_bus, sample_meal_with_nutrition, test_session
    ):
        """Test metadata edit can clear meal name."""
        meal = sample_meal_with_nutrition

        command = EditMealCommand(
            meal_id=meal.meal_id,
            dish_name="",
            food_item_changes=[],
        )

        result = await event_bus.send(command)

        assert result["success"] is True
        saved_meal = (
            test_session.query(MealORM).filter(MealORM.meal_id == meal.meal_id).one()
        )
        assert saved_meal.dish_name == ""

    @pytest.mark.asyncio
    async def test_edit_meal_unauthorized_user(
        self, event_bus, sample_meal_with_nutrition
    ):
        """Test editing meal with non-existent meal ID (simulates access denied)."""
        # Arrange - use a non-existent meal ID to simulate access denied
        command = EditMealCommand(
            meal_id="non-existent-meal-id",
            food_item_changes=[
                FoodItemChange(
                    action="add",
                    name="Test Ingredient",
                    quantity=100.0,
                    unit="g",
                    custom_nutrition=CustomNutritionData(
                        calories_per_100g=100.0,
                        protein_per_100g=5.0,
                        carbs_per_100g=10.0,
                        fat_per_100g=3.0,
                    ),
                )
            ],
        )

        # Act & Assert
        with pytest.raises(ResourceNotFoundException, match="Meal not found"):
            await event_bus.send(command)

    @pytest.mark.asyncio
    async def test_edit_meal_non_ready_status(self, event_bus, sample_meal_processing):
        """Test editing meal that's not in READY status."""
        # Arrange
        meal = sample_meal_processing
        command = EditMealCommand(
            meal_id=meal.meal_id,
            food_item_changes=[
                FoodItemChange(
                    action="add",
                    name="Test Ingredient",
                    quantity=100.0,
                    unit="g",
                    custom_nutrition=CustomNutritionData(
                        calories_per_100g=100.0,
                        protein_per_100g=5.0,
                        carbs_per_100g=10.0,
                        fat_per_100g=3.0,
                    ),
                )
            ],
        )

        # Act & Assert
        with pytest.raises(
            ValidationException, match="Meal must be in READY status to edit"
        ):
            await event_bus.send(command)

    @pytest.mark.asyncio
    async def test_edit_meal_nonexistent_meal(self, event_bus):
        """Test editing non-existent meal."""
        # Arrange
        command = EditMealCommand(
            meal_id="non-existent-meal-id",
            food_item_changes=[
                FoodItemChange(
                    action="add",
                    name="Test Ingredient",
                    quantity=100.0,
                    unit="g",
                    custom_nutrition=CustomNutritionData(
                        calories_per_100g=100.0,
                        protein_per_100g=5.0,
                        carbs_per_100g=10.0,
                        fat_per_100g=3.0,
                    ),
                )
            ],
        )

        # Act & Assert
        with pytest.raises(ResourceNotFoundException, match="Meal not found"):
            await event_bus.send(command)

    def test_realigns_legacy_translation_list_after_remove(
        self, sample_meal_with_nutrition
    ):
        """Removing an item should not drop every translated ingredient to English."""
        meal = sample_meal_with_nutrition
        original_items = meal.nutrition.food_items
        translated_names = [
            f"vi ingredient {index}" for index, _ in enumerate(original_items)
        ]
        meal.translations = {
            "vi": MealTranslation(
                meal_id=meal.meal_id,
                language="vi",
                dish_name="Bữa ăn",
                food_items=[],
                meal_ingredients=translated_names,
            )
        }
        updated_items = original_items[1:]
        handler = EditMealCommandHandler(uow=None)

        handler._realign_translations_after_food_item_changes(meal, updated_items)

        translation = meal.translations["vi"]
        assert translation.meal_ingredients == translated_names[1:]
        assert len(translation.food_items) == len(updated_items)
        assert translation.food_items[0].food_item_id == str(original_items[1].id)
        assert translation.food_items[0].name == translated_names[1]

    def test_realign_does_not_fill_missing_names_with_english_identity(
        self, sample_meal_with_nutrition
    ):
        meal = sample_meal_with_nutrition
        original_items = meal.nutrition.food_items
        translated_names = [
            f"vi ingredient {index}" for index, _ in enumerate(original_items)
        ]
        meal.translations = {
            "vi": MealTranslation(
                meal_id=meal.meal_id,
                language="vi",
                dish_name="Bữa ăn",
                food_items=[],
                meal_ingredients=list(translated_names),
            )
        }
        added_item = FoodItem(
            id="added-avocado",
            name="Avocado",
            quantity=50.0,
            unit="g",
            macros=Macros(protein=1.0, carbs=4.0, fat=7.0),
        )
        handler = EditMealCommandHandler(uow=None)

        handler._realign_translations_after_food_item_changes(
            meal, [*original_items, added_item]
        )

        translation = meal.translations["vi"]
        assert translation.meal_ingredients == translated_names
        assert "Avocado" not in translation.meal_ingredients


@pytest.mark.unit
class TestAddCustomIngredientCommandHandler:
    """Test AddCustomIngredientCommand handler."""

    @pytest.mark.asyncio
    async def test_add_custom_ingredient_success(
        self, event_bus, sample_meal_with_nutrition
    ):
        """Test successful custom ingredient addition."""
        # Arrange
        meal = sample_meal_with_nutrition

        command = AddCustomIngredientCommand(
            meal_id=meal.meal_id,
            name="Homemade Dressing",
            quantity=30.0,
            unit="ml",
            nutrition=CustomNutritionData(
                calories_per_100g=400.0,
                protein_per_100g=1.0,
                carbs_per_100g=5.0,
                fat_per_100g=42.0,
            ),
        )

        # Act
        result = await event_bus.send(command)

        # Assert - AddCustomIngredientCommandHandler returns a simple response
        assert result["success"] is True
        assert result["meal_id"] == meal.meal_id
        assert "message" in result
        assert "Added custom ingredient: Homemade Dressing" in result["message"]

    @pytest.mark.asyncio
    async def test_add_custom_ingredient_unauthorized(
        self, event_bus, sample_meal_with_nutrition
    ):
        """Test adding custom ingredient with non-existent meal ID (simulates access denied)."""
        # Arrange - use a non-existent meal ID to simulate access denied
        command = AddCustomIngredientCommand(
            meal_id="non-existent-meal-id",
            name="Test Ingredient",
            quantity=100.0,
            unit="g",
            nutrition=CustomNutritionData(
                calories_per_100g=100.0,
                protein_per_100g=5.0,
                carbs_per_100g=10.0,
                fat_per_100g=3.0,
            ),
        )

        # Act & Assert - MealService raises ValueError when meal not found
        with pytest.raises(ValueError, match="Meal.*not found"):
            await event_bus.send(command)
