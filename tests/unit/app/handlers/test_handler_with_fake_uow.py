"""
Unit tests demonstrating FakeUoW pattern for handler testing.
These tests show how handlers can be tested without a database.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.app.commands.meal import DeleteMealCommand
from src.app.handlers.command_handlers.delete_meal_command_handler import (
    DeleteMealCommandHandler,
)
from src.domain.model.meal import Meal, MealImage


class TestDeleteMealWithFakeUoW:
    """Test DeleteMealCommandHandler using Mock UoW (handler requires session access)."""

    @pytest.mark.asyncio
    async def test_delete_meal_marks_as_inactive(self):
        """Test that deleting a meal delegates to repository delete."""
        # Arrange
        user_id = str(uuid4())

        meal = Meal.create_new_processing(
            user_id=user_id,
            image=MealImage(
                image_id=str(uuid4()),
                format="jpeg",
                size_bytes=100000,
                url="https://example.com/image.jpg",
            ),
        )
        from src.domain.model.nutrition import Macros, Nutrition

        meal = meal.mark_ready(
            nutrition=Nutrition(
                macros=Macros(protein=30, carbs=50, fat=20), food_items=[]
            ),
            dish_name="Test Meal",
        )

        # Build a mock UoW — handler now delegates deletion to uow.meals.delete()
        mock_uow = MagicMock()
        mock_uow.meals.find_by_id = AsyncMock(return_value=meal)
        mock_uow.meals.delete = AsyncMock(return_value=None)
        mock_uow.meal_recommendation_plans.clear_links_for_deleted_meal = AsyncMock(
            return_value=None
        )
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=False)

        handler = DeleteMealCommandHandler(
            uow=mock_uow, event_publisher=MagicMock(publish=AsyncMock())
        )

        # Act
        command = DeleteMealCommand(meal_id=meal.meal_id, user_id=user_id)
        result = await handler.handle(command)

        # Assert
        assert result["meal_id"] == meal.meal_id
        assert "deleted" in result["message"].lower()
        mock_uow.meal_recommendation_plans.clear_links_for_deleted_meal.assert_awaited_once_with(
            meal_id=meal.meal_id
        )
        mock_uow.meals.delete.assert_called_once_with(meal.meal_id)

    @pytest.mark.asyncio
    async def test_delete_meal_clears_recommendation_links_before_delete(self):
        """Recommended-meal FK clear must run before hard-deleting the meal."""
        user_id = str(uuid4())
        meal = Meal.create_new_processing(
            user_id=user_id,
            image=MealImage(
                image_id=str(uuid4()),
                format="jpeg",
                size_bytes=100000,
                url="https://example.com/image.jpg",
            ),
        )
        from src.domain.model.nutrition import Macros, Nutrition

        meal = meal.mark_ready(
            nutrition=Nutrition(
                macros=Macros(protein=30, carbs=50, fat=20), food_items=[]
            ),
            dish_name="Recommended meal",
        )
        meal = meal.__class__(**{**meal.__dict__, "source": "meal_recommendation"})

        call_order: list[str] = []

        async def clear_links(*, meal_id: str) -> None:
            call_order.append(f"clear:{meal_id}")

        async def delete_meal(meal_id: str) -> None:
            call_order.append(f"delete:{meal_id}")

        mock_uow = MagicMock()
        mock_uow.meals.find_by_id = AsyncMock(return_value=meal)
        mock_uow.meals.delete = AsyncMock(side_effect=delete_meal)
        mock_uow.meal_recommendation_plans.clear_links_for_deleted_meal = AsyncMock(
            side_effect=clear_links
        )
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=False)

        handler = DeleteMealCommandHandler(
            uow=mock_uow, event_publisher=MagicMock(publish=AsyncMock())
        )
        await handler.handle(DeleteMealCommand(meal_id=meal.meal_id, user_id=user_id))

        assert call_order == [f"clear:{meal.meal_id}", f"delete:{meal.meal_id}"]


# Removed TestSyncUserWithFakeUoW - SyncUserCommand not in scope for this demo


class TestFakeUoWTransactionBehavior:
    """Test FakeUnitOfWork transaction behavior."""

    def test_commit_sets_flag(self):
        """Test that commit sets the committed flag."""
        fake_uow = FakeUnitOfWork()

        with fake_uow:
            fake_uow._sync_commit()

        assert fake_uow.committed is True
        assert fake_uow.rolled_back is False

    def test_rollback_sets_flag(self):
        """Test that rollback sets the rolled_back flag."""
        fake_uow = FakeUnitOfWork()

        with fake_uow:
            fake_uow._sync_rollback()

        assert fake_uow.rolled_back is True

    def test_context_manager_commits_on_success(self):
        """Test that context manager commits on successful execution."""
        fake_uow = FakeUnitOfWork()

        with fake_uow:
            # Simulate successful operation
            pass

        assert fake_uow.committed is True

    def test_context_manager_rollsback_on_exception(self):
        """Test that context manager rolls back on exception."""
        fake_uow = FakeUnitOfWork()

        try:
            with fake_uow:
                raise ValueError("Test error")
        except ValueError:
            pass

        assert fake_uow.rolled_back is True
