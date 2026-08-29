"""
Unit tests for TDEE helper functions in suggestion_tdee_helpers.py.
"""
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.domain.cache.cache_keys import CacheKeys
from src.domain.services.meal_suggestion.suggestion_tdee_helpers import (
    calculate_daily_tdee,
    get_adjusted_daily_target,
)


@pytest.fixture
def mock_tdee_service():
    """Create mock TDEE calculation service."""
    service = Mock()
    result = Mock()
    result.macros.calories = 2200.0
    result.macros.protein = 150.0
    result.macros.carbs = 220.0
    result.macros.fat = 70.0
    result.bmr = 1800.0
    service.calculate_tdee.return_value = result
    return service


@pytest.fixture
def mock_profile():
    """Create mock user profile."""
    profile = Mock()
    profile.age = 30
    profile.gender = "male"
    profile.height_cm = 175
    profile.weight_kg = 75
    profile.job_type = "desk"
    profile.training_days_per_week = 4
    profile.training_minutes_per_session = 60
    profile.training_level = "intermediate"
    profile.fitness_goal = "recomp"
    profile.body_fat_percentage = 18
    return profile


class TestCalculateDailyTdee:
    """Test calculate_daily_tdee helper function."""

    def test_returns_calories_from_service(self, mock_tdee_service, mock_profile):
        """Should return calories from TDEE service calculation."""
        result = calculate_daily_tdee(mock_tdee_service, mock_profile)
        assert result == 2200.0
        mock_tdee_service.calculate_tdee.assert_called_once()

    def test_propagates_authoritative_tdee_error(self, mock_profile):
        """A complete profile must not receive a fabricated fallback target."""
        failing_service = Mock()
        failing_service.calculate_tdee.side_effect = Exception("TDEE error")

        with pytest.raises(Exception, match="TDEE error"):
            calculate_daily_tdee(failing_service, mock_profile)


class TestGetAdjustedDailyTarget:
    """Test get_adjusted_daily_target helper function."""

    @pytest.mark.asyncio
    async def test_returns_adjusted_when_budget_exists(self, mock_tdee_service, mock_profile):
        """Should return adjusted calories when weekly budget exists."""
        from datetime import date

        from src.domain.services.weekly_budget_service import (
            AdjustedDailyTargets,
            EffectiveAdjustedResult,
        )

        mock_budget = Mock()
        mock_profile.profile_target_revision = 1
        mock_budget.target_revision = 1
        mock_adjusted = AdjustedDailyTargets(
            calories=2100.0, carbs=200, fat=70, protein=70,
            bmr_floor_active=False, remaining_days=5,
        )
        mock_effective = EffectiveAdjustedResult(
            adjusted=mock_adjusted,
            consumed_before_today={"calories": 0, "protein": 0, "carbs": 0, "fat": 0},
            consumed_total={"calories": 0, "protein": 0, "carbs": 0, "fat": 0},
            logged_past_days=0, skipped_days=0, show_logging_prompt=False,
        )

        mock_uow = Mock()
        mock_uow.weekly_budgets.find_by_user_and_week = AsyncMock(return_value=mock_budget)
        mock_uow.users.get_weekly_auto_adjust = AsyncMock(return_value=True)

        with patch("src.domain.services.meal_suggestion.suggestion_tdee_helpers.WeeklyBudgetService") as mock_budget_svc, \
             patch("src.domain.services.meal_suggestion.suggestion_tdee_helpers.get_user_monday_async", new_callable=AsyncMock, return_value=date(2026, 3, 9)), \
             patch("src.domain.services.meal_suggestion.suggestion_tdee_helpers.resolve_user_timezone_async", new_callable=AsyncMock, return_value="UTC"), \
             patch("src.domain.services.meal_suggestion.suggestion_tdee_helpers.user_today", return_value=date(2026, 3, 13)):

            mock_budget_svc.get_effective_adjusted_daily_async = AsyncMock(return_value=mock_effective)

            result = await get_adjusted_daily_target(mock_tdee_service, "user123", mock_profile, uow=mock_uow)

        assert result == 2100.0

    @pytest.mark.asyncio
    async def test_falls_back_to_raw_tdee_when_no_budget(self, mock_tdee_service, mock_profile):
        """Should fall back to raw TDEE when no weekly budget found."""
        from datetime import date

        mock_uow = Mock()
        mock_uow.weekly_budgets.find_by_user_and_week = AsyncMock(return_value=None)

        with patch("src.domain.services.meal_suggestion.suggestion_tdee_helpers.resolve_user_timezone_async", new_callable=AsyncMock, return_value="UTC"), \
             patch("src.domain.services.meal_suggestion.suggestion_tdee_helpers.user_today", return_value=date(2026, 3, 13)), \
             patch("src.domain.services.meal_suggestion.suggestion_tdee_helpers.get_user_monday_async", new_callable=AsyncMock, return_value=date(2026, 3, 9)):

            result = await get_adjusted_daily_target(mock_tdee_service, "user123", mock_profile, uow=mock_uow)

        assert result == 2200.0

    @pytest.mark.asyncio
    async def test_falls_back_to_raw_tdee_when_no_uow(self, mock_tdee_service, mock_profile):
        """Should fall back to raw TDEE when no UoW provided."""
        result = await get_adjusted_daily_target(mock_tdee_service, "user123", mock_profile)
        assert result == 2200.0

    @pytest.mark.asyncio
    async def test_falls_back_to_calculate_daily_tdee_on_error(self, mock_profile):
        """Should fall back to calculate_daily_tdee if adjusted target raises."""
        failing_service = Mock()
        failing_service.calculate_tdee.side_effect = [Exception("TDEE error"), mock_tdee_result()]

        result = await get_adjusted_daily_target(failing_service, "user123", mock_profile)

        assert result == 2200.0
        assert failing_service.calculate_tdee.call_count == 2


def mock_tdee_result():
    result = Mock()
    result.macros.calories = 2200.0
    return result


class TestCacheKeyGeneration:
    """Test cache key generation."""

    def test_user_tdee_key_format(self):
        """TDEE cache key should have correct format."""
        key, ttl = CacheKeys.user_tdee("user123")

        assert key == "user:tdee_v2:user123"
        assert ttl == CacheKeys.TTL_1_DAY

    def test_user_tdee_key_unique_per_user(self):
        """Different users should have different cache keys."""
        key1, _ = CacheKeys.user_tdee("user1")
        key2, _ = CacheKeys.user_tdee("user2")

        assert key1 != key2
