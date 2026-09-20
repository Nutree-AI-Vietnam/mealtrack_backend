"""
Test user-specific daily activities functionality.
"""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.handlers.query_handlers import GetDailyActivitiesQueryHandler
from src.app.queries.activity.get_daily_activities_query import GetDailyActivitiesQuery
from src.domain.model import Macros, Meal, MealImage, MealStatus, Nutrition
from src.domain.model.meal_projection import MealProjection


@pytest.mark.asyncio
class TestUserSpecificActivities:
    """Test user-specific daily activities functionality."""

    async def test_activities_filtered_by_user(self):
        """Test that activities are properly filtered by user_id."""
        # Create mock meals for different users
        user1_meal = Meal(
            meal_id="123e4567-e89b-12d3-a456-426614174001",
            user_id="123e4567-e89b-12d3-a456-426614174100",
            status=MealStatus.READY,
            created_at=datetime(2024, 8, 15, 12, 0, 0),
            image=MealImage(
                image_id="123e4567-e89b-12d3-a456-426614174010",
                format="jpeg",
                size_bytes=100000,
                url="https://example.com/img1.jpg",
            ),
            dish_name="User 1 Lunch",
            nutrition=Nutrition(
                macros=Macros(protein=30.0, carbs=50.0, fat=20.0),
                food_items=[],
                confidence_score=0.9,
            ),
            ready_at=datetime(2024, 8, 15, 12, 5, 0),
        )

        user2_meal = Meal(
            meal_id="123e4567-e89b-12d3-a456-426614174002",
            user_id="123e4567-e89b-12d3-a456-426614174200",
            status=MealStatus.READY,
            created_at=datetime(2024, 8, 15, 13, 0, 0),
            image=MealImage(
                image_id="123e4567-e89b-12d3-a456-426614174020",
                format="jpeg",
                size_bytes=200000,
                url="https://example.com/img2.jpg",
            ),
            dish_name="User 2 Lunch",
            nutrition=Nutrition(
                macros=Macros(protein=35.0, carbs=60.0, fat=25.0),
                food_items=[],
                confidence_score=0.95,
            ),
            ready_at=datetime(2024, 8, 15, 13, 5, 0),
        )

        # Create mock repository (async)
        mock_meals_repo = AsyncMock()

        # Configure repository to return different meals for different users
        async def mock_find_by_date(
            target_date, user_id=None, limit=50, user_timezone=None, projection=None
        ):
            if user_id == "123e4567-e89b-12d3-a456-426614174100":
                return [user1_meal]
            elif user_id == "123e4567-e89b-12d3-a456-426614174200":
                return [user2_meal]
            else:
                return [user1_meal, user2_meal]

        mock_meals_repo.find_by_date.side_effect = mock_find_by_date

        # Create mock UnitOfWork with users repo for timezone lookup (async)
        mock_users_repo = AsyncMock()
        mock_users_repo.find_by_id.return_value = None  # No timezone stored

        mock_uow = AsyncMock()
        mock_uow.meals = mock_meals_repo
        mock_uow.users = mock_users_repo
        mock_uow.hydration_entries.find_by_date = AsyncMock(return_value=[])
        mock_uow.movement_entries.find_by_user_and_logged_range = AsyncMock(
            return_value=[]
        )
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=False)

        # Create handler and patch AsyncUnitOfWork
        handler = GetDailyActivitiesQueryHandler()

        with patch(
            "src.app.handlers.query_handlers.get_daily_activities_query_handler.AsyncUnitOfWork",
            return_value=mock_uow,
        ):
            # Test for user 1
            query_user1 = GetDailyActivitiesQuery(
                user_id="123e4567-e89b-12d3-a456-426614174100",
                target_date=datetime(2024, 8, 15),
            )

            activities_user1 = await handler.handle(query_user1)

            # Verify user 1 only gets their meal
            assert len(activities_user1) == 1
            assert activities_user1[0]["id"] == "123e4567-e89b-12d3-a456-426614174001"
            assert activities_user1[0]["title"] == "User 1 Lunch"

            # Test for user 2
            query_user2 = GetDailyActivitiesQuery(
                user_id="123e4567-e89b-12d3-a456-426614174200",
                target_date=datetime(2024, 8, 15),
            )

            activities_user2 = await handler.handle(query_user2)

            # Verify user 2 only gets their meal
            assert len(activities_user2) == 1
            assert activities_user2[0]["id"] == "123e4567-e89b-12d3-a456-426614174002"
            assert activities_user2[0]["title"] == "User 2 Lunch"

            # Verify repository was called with correct parameters
            actual_calls = mock_meals_repo.find_by_date.call_args_list
            assert len(actual_calls) == 2

            # Check that user_id was passed correctly
            assert (
                actual_calls[0][1]["user_id"] == "123e4567-e89b-12d3-a456-426614174100"
            )
            assert (
                actual_calls[1][1]["user_id"] == "123e4567-e89b-12d3-a456-426614174200"
            )

    async def test_empty_activities_for_user_with_no_meals(self):
        """Test that users with no meals get empty activities list."""
        # Create mock repository that returns no meals (async)
        mock_meals_repo = AsyncMock()
        mock_meals_repo.find_by_date.return_value = []

        # Create mock UnitOfWork with users repo for timezone lookup (async)
        mock_users_repo = AsyncMock()
        mock_users_repo.find_by_id.return_value = None

        mock_uow = AsyncMock()
        mock_uow.meals = mock_meals_repo
        mock_uow.users = mock_users_repo
        mock_uow.hydration_entries.find_by_date = AsyncMock(return_value=[])
        mock_uow.movement_entries.find_by_user_and_logged_range = AsyncMock(
            return_value=[]
        )
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=False)

        # Create handler
        handler = GetDailyActivitiesQueryHandler()

        with patch(
            "src.app.handlers.query_handlers.get_daily_activities_query_handler.AsyncUnitOfWork",
            return_value=mock_uow,
        ):
            # Test query
            query = GetDailyActivitiesQuery(
                user_id="123e4567-e89b-12d3-a456-426614174300",
                target_date=datetime(2024, 8, 15),
            )

            activities = await handler.handle(query)

            # Verify empty result
            assert activities == []

            # Verify repository was called with correct user_id
            mock_meals_repo.find_by_date.assert_called_once_with(
                date(2024, 8, 15),
                user_id="123e4567-e89b-12d3-a456-426614174300",
                user_timezone="UTC",
                projection=MealProjection.LIST_CARD,
            )

    async def test_daily_activities_uses_short_lived_uow_scopes(self):
        """Cache misses should open one shared read UoW for timezone, meals, and movement."""

        class _UowScope:
            def __init__(
                self,
                *,
                users=None,
                meals=None,
                movement_entries=None,
                hydration_entries=None,
            ):
                self.users = users or AsyncMock()
                self.meals = meals or AsyncMock()
                self.movement_entries = movement_entries or AsyncMock()
                self.hydration_entries = hydration_entries or AsyncMock()
                self.enter = AsyncMock()
                self.exit = AsyncMock()

            async def __aenter__(self):
                await self.enter()
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                await self.exit(exc_type, exc_val, exc_tb)
                return False

        users_repo = AsyncMock()
        users_repo.find_by_id.return_value = None

        meals_repo = AsyncMock()
        meals_repo.find_by_date.return_value = []

        hydration_entries_repo = AsyncMock()
        hydration_entries_repo.find_by_date.return_value = []

        movement_repo = AsyncMock()
        movement_repo.find_by_user_and_logged_range.return_value = []

        scopes = [
            _UowScope(
                users=users_repo,
                meals=meals_repo,
                hydration_entries=hydration_entries_repo,
                movement_entries=movement_repo,
            ),
        ]
        uow_factory = MagicMock(side_effect=scopes)

        handler = GetDailyActivitiesQueryHandler()
        query = GetDailyActivitiesQuery(
            user_id="123e4567-e89b-12d3-a456-426614174300",
            target_date=datetime(2024, 8, 15),
        )

        with patch(
            "src.app.handlers.query_handlers.get_daily_activities_query_handler.AsyncUnitOfWork",
            uow_factory,
        ):
            activities = await handler.handle(query)

        assert activities == []
        assert uow_factory.call_count == 1
        for scope in scopes:
            scope.enter.assert_awaited_once()
            scope.exit.assert_awaited_once()
        meals_repo.find_by_date.assert_awaited_once_with(
            date(2024, 8, 15),
            user_id="123e4567-e89b-12d3-a456-426614174300",
            user_timezone="UTC",
            projection=MealProjection.LIST_CARD,
        )
        movement_repo.find_by_user_and_logged_range.assert_awaited_once()
