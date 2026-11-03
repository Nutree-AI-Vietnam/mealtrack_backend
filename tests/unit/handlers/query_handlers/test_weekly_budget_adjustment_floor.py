"""Weekly auto-adjust never sets a day below the clinical minimum."""

from datetime import date

from src.app.handlers.query_handlers.get_weekly_budget_query_handler import (
    _adjustment_floor,
)
from src.domain.model.weekly import WeeklyMacroBudget
from src.domain.services.weekly_budget_service import WeeklyBudgetService


def test_floor_uses_the_clinical_minimum_when_above_bmr():
    assert _adjustment_floor({"bmr": 1149.0, "calorie_floor": 1200.0}) == 1200.0
    assert _adjustment_floor({"bmr": 1300.0, "calorie_floor": 1300.0}) == 1300.0


def test_cached_results_without_a_floor_fall_back_to_bmr():
    assert _adjustment_floor({"bmr": 1149.0}) == 1149.0


def test_an_overeating_week_cannot_push_a_1200_kcal_base_below_1200():
    budget = WeeklyMacroBudget(
        weekly_budget_id="b",
        user_id="u",
        week_start_date=date(2026, 11, 2),
        target_calories=8400,
        target_protein=700,
        target_carbs=700,
        target_fat=350,
        consumed_calories=6000,
        consumed_protein=500,
        consumed_carbs=500,
        consumed_fat=250,
    )
    adjusted = WeeklyBudgetService.calculate_adjusted_daily(
        budget,
        standard_daily_calories=1200,
        standard_daily_carbs=100,
        standard_daily_fat=50,
        standard_daily_protein=100,
        bmr=_adjustment_floor({"bmr": 1149.0, "calorie_floor": 1200.0}),
        remaining_days=3,
    )

    assert adjusted.calories >= 1199.5
