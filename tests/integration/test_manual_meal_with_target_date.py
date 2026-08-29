"""
Integration test for manual meal creation with target date.
"""

import uuid
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.commands.meal.create_manual_meal_command import (
    CreateManualMealCommand,
    CustomNutrition,
    ManualMealItem,
)
from src.app.handlers.command_handlers.create_manual_meal_command_handler import (
    CreateManualMealCommandHandler,
)
from src.app.handlers.query_handlers.get_daily_activities_query_handler import (
    GetDailyActivitiesQueryHandler,
)
from src.app.queries.activity import GetDailyActivitiesQuery
from src.domain.model import MealStatus


class _ManualMealUow:
    """Minimal async UoW for exercising the current manual-meal handler contract."""

    def __init__(self, meals):
        self.meals = meals
        self.users = MagicMock()
        self.users.find_by_id = AsyncMock(return_value=None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        return None

    async def rollback(self):
        return None


@pytest.mark.asyncio
async def test_manual_meal_created_with_target_date():
    """Test that manual meals are created with the specified target date."""
    # Arrange
    mock_meal_repo = MagicMock()

    # Mock repository save - capture the saved meal
    saved_meal = None

    def save_meal(meal):
        nonlocal saved_meal
        saved_meal = meal
        return meal

    mock_meal_repo.insert = AsyncMock(side_effect=save_meal)
    publisher = MagicMock()
    publisher.publish = AsyncMock()

    # Create handler
    handler = CreateManualMealCommandHandler(
        uow=_ManualMealUow(mock_meal_repo),
        event_publisher=publisher,
        meal_repository=mock_meal_repo,
    )

    # Target date is yesterday
    target_date = date.today() - timedelta(days=1)

    # Create command with target date
    test_user_id = str(uuid.uuid4())
    command = CreateManualMealCommand(
        user_id=test_user_id,
        items=[
            ManualMealItem(
                name="Chicken Breast",
                quantity=150.0,
                unit="g",
                custom_nutrition=CustomNutrition(
                    calories_per_100g=165.0,
                    protein_per_100g=31.0,
                    carbs_per_100g=0.0,
                    fat_per_100g=3.6,
                ),
            )
        ],
        dish_name="Grilled Chicken",
        meal_type="lunch",
        target_date=target_date,
    )

    # Act
    await handler.handle(command)

    # Assert
    assert saved_meal is not None
    assert saved_meal.created_at.date() == target_date
    assert saved_meal.ready_at.date() == target_date
    assert saved_meal.meal_type == "lunch"
    assert saved_meal.dish_name == "Grilled Chicken"
    assert saved_meal.user_id == test_user_id

    # Verify nutrition was calculated correctly
    assert saved_meal.nutrition is not None
    assert saved_meal.nutrition.calories > 0
    assert saved_meal.nutrition.macros.protein > 0


@pytest.mark.asyncio
async def test_manual_meal_appears_in_daily_activities():
    """Test that manual meals appear in daily activities for the target date."""
    # Arrange
    target_date = date.today() - timedelta(days=1)
    target_datetime = datetime.combine(target_date, datetime.now().time())
    test_user_id = str(uuid.uuid4())
    test_meal_id = str(uuid.uuid4())

    # Create a mock meal with target date
    mock_meal = MagicMock()
    mock_meal.meal_id = test_meal_id
    mock_meal.user_id = test_user_id
    mock_meal.created_at = target_datetime
    mock_meal.dish_name = "Grilled Chicken"
    mock_meal.meal_type = "lunch"
    mock_meal.status = MealStatus.READY  # Use actual enum, not MagicMock
    mock_meal.nutrition = MagicMock()
    mock_meal.nutrition.calories = 247.5
    mock_meal.nutrition.macros = MagicMock()
    mock_meal.nutrition.macros.protein = 46.5
    mock_meal.nutrition.macros.carbs = 0.0
    mock_meal.nutrition.macros.fat = 5.4
    mock_meal.nutrition.food_items = []
    mock_meal.image = MagicMock()
    mock_meal.image.url = None

    # Mock find_by_date to return the meal when called with any arguments
    def find_by_date_mock(date_obj, user_id, **kwargs):
        if user_id == test_user_id:
            return [mock_meal]
        return []

    mock_meals_repo = MagicMock()
    mock_meals_repo.find_by_date = AsyncMock(side_effect=find_by_date_mock)
    mock_hydration_repo = MagicMock()
    mock_hydration_repo.find_by_date = AsyncMock(return_value=[])
    mock_movement_repo = MagicMock()
    mock_movement_repo.find_by_user_and_logged_range = AsyncMock(return_value=[])
    mock_uow = MagicMock()
    mock_uow.users.find_by_id = AsyncMock(return_value=None)
    mock_uow.meals = mock_meals_repo
    mock_uow.hydration_entries = mock_hydration_repo
    mock_uow.movement_entries = mock_movement_repo
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)

    # Create query handler
    handler = GetDailyActivitiesQueryHandler()

    # Create query
    query = GetDailyActivitiesQuery(user_id=test_user_id, target_date=target_datetime)

    # Act
    with patch(
        "src.app.handlers.query_handlers.get_daily_activities_query_handler.AsyncUnitOfWork",
        return_value=mock_uow,
    ):
        activities = await handler.handle(query)

    # Assert
    assert len(activities) == 1
    activity = activities[0]
    assert activity["id"] == test_meal_id
    assert activity["type"] == "meal"
    assert activity["title"] == "Grilled Chicken"
    assert activity["meal_type"] == "lunch"
    assert activity["calories"] == 234.6
    assert activity["macros"]["protein"] == 46.5
    assert activity["macros"]["carbs"] == 0.0
    assert activity["macros"]["fat"] == 5.4


@pytest.mark.asyncio
async def test_manual_meal_without_target_date_uses_current_date():
    """Test that manual meals without target date use current date."""
    # Arrange
    mock_meal_repo = MagicMock()

    saved_meal = None

    def save_meal(meal):
        nonlocal saved_meal
        saved_meal = meal
        return meal

    mock_meal_repo.insert = AsyncMock(side_effect=save_meal)
    publisher = MagicMock()
    publisher.publish = AsyncMock()

    handler = CreateManualMealCommandHandler(
        uow=_ManualMealUow(mock_meal_repo),
        event_publisher=publisher,
        meal_repository=mock_meal_repo,
    )

    # Create command WITHOUT target date
    test_user_id = str(uuid.uuid4())
    command = CreateManualMealCommand(
        user_id=test_user_id,
        items=[
            ManualMealItem(
                name="Chicken Breast",
                quantity=150.0,
                unit="g",
                custom_nutrition=CustomNutrition(
                    calories_per_100g=165.0,
                    protein_per_100g=31.0,
                    carbs_per_100g=0.0,
                    fat_per_100g=3.6,
                ),
            )
        ],
        dish_name="Grilled Chicken",
        meal_type="lunch",
        target_date=None,  # No target date
    )

    # Act
    await handler.handle(command)

    # Assert
    assert saved_meal is not None
    assert saved_meal.created_at.date() == date.today()
    assert saved_meal.ready_at.date() == date.today()
