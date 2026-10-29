"""
Unit tests for meal edit command handlers.
"""

import json
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from src.api.exceptions import ResourceNotFoundException, ValidationException
from src.api.mappers.meal_mapper import MealMapper
from src.app.commands.meal import (
    AddCustomIngredientCommand,
    CustomNutritionData,
    EditMealCommand,
    FoodItemChange,
)
from src.app.events.meal import MealEditedEvent
from src.app.events.meal.meal_events import register_local_cache_invalidation_hook
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
    async def test_moved_meal_time_survives_a_reload_and_purges_both_local_days(
        self, event_bus, sample_meal_with_nutrition, sample_user, test_session
    ):
        """06:30 on Oct 28 in Vietnam is 23:30 UTC on Oct 27.

        Moving that breakfast to noon on Oct 27 must purge Oct 28 and Oct 27,
        the local days the day caches are keyed by, not the UTC dates.
        """
        meal = sample_meal_with_nutrition
        sample_user.timezone = "Asia/Ho_Chi_Minh"
        row = test_session.query(MealORM).filter(MealORM.meal_id == meal.meal_id).one()
        row.created_at = datetime(2026, 10, 27, 23, 30)
        row.meal_type = "breakfast"
        test_session.commit()
        purged = []

        async def record_purge(user_id, meal_date, old_meal_date):
            purged.append((user_id, meal_date, old_meal_date))

        register_local_cache_invalidation_hook(record_purge)
        try:
            await event_bus.send(
                EditMealCommand(
                    meal_id=meal.meal_id,
                    created_at=datetime(2026, 10, 27, 5, 0, tzinfo=UTC),
                    meal_type="lunch",
                    food_item_changes=[],
                )
            )
        finally:
            register_local_cache_invalidation_hook(None)

        test_session.expire_all()
        row = test_session.query(MealORM).filter(MealORM.meal_id == meal.meal_id).one()
        assert row.created_at.replace(tzinfo=None) == datetime(2026, 10, 27, 5, 0)
        assert row.meal_type == "lunch"
        assert purged == [(meal.user_id, date(2026, 10, 27), date(2026, 10, 28))]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("logged_on_day", "new_time"),
        [
            # A real log moved to exactly noon looks like a backfill stamp.
            (True, datetime(2026, 10, 28, 12, 0)),
            # A backfill moved off noon looks like a real-time log.
            (False, datetime(2026, 10, 28, 8, 17)),
        ],
    )
    async def test_an_edit_of_the_meal_time_keeps_whether_it_was_logged_on_its_day(
        self,
        event_bus,
        sample_meal_with_nutrition,
        test_session,
        logged_on_day,
        new_time,
    ):
        meal = sample_meal_with_nutrition
        row = test_session.query(MealORM).filter(MealORM.meal_id == meal.meal_id).one()
        row.logged_on_day = logged_on_day
        test_session.commit()

        result = await event_bus.send(
            EditMealCommand(
                meal_id=meal.meal_id, created_at=new_time, food_item_changes=[]
            )
        )

        assert result["success"] is True
        test_session.expire_all()
        row = test_session.query(MealORM).filter(MealORM.meal_id == meal.meal_id).one()
        assert row.logged_on_day is logged_on_day

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

    @pytest.mark.asyncio
    async def test_rename_is_what_a_vietnamese_read_shows(
        self, event_bus, sample_meal_with_nutrition, test_session
    ):
        """A rename must beat the localized name the AI stored at scan time."""
        meal = sample_meal_with_nutrition
        meal_row = (
            test_session.query(MealORM).filter(MealORM.meal_id == meal.meal_id).one()
        )
        meal_row.dish_name = "Sản phẩm thực phẩm Việt Nam"
        meal_row.raw_ai_response = json.dumps(
            {
                "dish_name": "Vietnamese food product",
                "localized_language": "vi",
                "localized_dish_name": "Sản phẩm thực phẩm Việt Nam",
                "foods": [
                    {"name": item.name, "localized_name": f"vi {item.name}"}
                    for item in meal.nutrition.food_items
                ],
            },
            ensure_ascii=False,
        )
        test_session.commit()

        await event_bus.send(
            EditMealCommand(
                meal_id=meal.meal_id,
                dish_name="Bánh mì của mẹ",
                food_item_changes=[],
                language="vi",
            )
        )

        test_session.expire_all()
        row = test_session.query(MealORM).filter(MealORM.meal_id == meal.meal_id).one()
        saved = replace(meal, dish_name=row.dish_name, raw_gpt_json=row.raw_ai_response)
        for language in ("vi", "en"):
            response = MealMapper.to_detailed_response(saved, target_language=language)
            assert response.dish_name == "Bánh mì của mẹ"

    def test_rename_overwrites_stale_translation_rows(self, sample_meal_with_nutrition):
        meal = sample_meal_with_nutrition
        meal.translations = {
            "vi": MealTranslation(
                meal_id=meal.meal_id,
                language="vi",
                dish_name="Sản phẩm thực phẩm Việt Nam",
                food_items=[],
            )
        }
        handler = EditMealCommandHandler(uow=None)
        command = EditMealCommand(
            meal_id=meal.meal_id, dish_name="Bánh mì của mẹ", language="vi"
        )
        edited = meal.mark_edited(nutrition=meal.nutrition, dish_name="Bánh mì của mẹ")

        renamed = handler._carry_saved_name_to_locales(edited, command)

        assert renamed.translations["vi"].dish_name == "Bánh mì của mẹ"
        assert meal.translations["vi"].dish_name == "Sản phẩm thực phẩm Việt Nam"

    def test_saved_name_is_every_locale_name_without_a_rename_check(
        self, sample_meal_with_nutrition
    ):
        """The name in the save is the name, even one another locale holds."""
        meal = sample_meal_with_nutrition
        meal.translations = {
            language: MealTranslation(
                meal_id=meal.meal_id,
                language=language,
                dish_name=name,
                food_items=[],
            )
            for language, name in (("vi", "Gà nướng với cơm"), ("es", "Pollo asado"))
        }
        handler = EditMealCommandHandler(uow=None)
        command = EditMealCommand(
            meal_id=meal.meal_id, dish_name="Gà nướng với cơm", language="vi"
        )
        edited = meal.mark_edited(
            nutrition=meal.nutrition, dish_name="Gà nướng với cơm"
        )

        saved = handler._carry_saved_name_to_locales(edited, command)

        assert {t.dish_name for t in saved.translations.values()} == {
            "Gà nướng với cơm"
        }

    @pytest.mark.parametrize("dish_name", [None, "", "   "])
    def test_save_without_a_name_leaves_locale_names_alone(
        self, sample_meal_with_nutrition, dish_name
    ):
        meal = sample_meal_with_nutrition
        handler = EditMealCommandHandler(uow=None)
        command = EditMealCommand(
            meal_id=meal.meal_id, dish_name=dish_name, language="vi"
        )
        edited = meal.mark_edited(nutrition=meal.nutrition, dish_name=meal.dish_name)

        assert handler._carry_saved_name_to_locales(edited, command) is edited

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
