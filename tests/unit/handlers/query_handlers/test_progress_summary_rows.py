"""Derived-calorie precision for progress summary day rows."""

from datetime import UTC, date, datetime

from src.app.handlers.query_handlers.progress_summary_rows import build_progress_day_row
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.nutrition import Nutrition
from src.domain.model.nutrition.macros import Macros


def _meal(protein: float, carbs: float, fat: float) -> Meal:
    created = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    return Meal(
        meal_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        status=MealStatus.READY,
        created_at=created,
        image=None,
        dish_name="Test",
        nutrition=Nutrition(macros=Macros(protein=protein, carbs=carbs, fat=fat)),
        ready_at=created,
    )


def test_calories_derived_from_macros_one_decimal():
    # 10*4 + 20*4 + 5*9 = 165.0 (no fiber)
    row = build_progress_day_row(
        date(2026, 9, 1),
        meals=[_meal(10.0, 20.0, 5.0)],
        burned_calories=120.0,
        hydration_ml=1500,
        hydration_goal_ml=2000,
        protein_target_g=140.0,
        target_calories=1850.0,
        target_source="base",
        is_cheat_day=False,
    )
    expected = Macros(protein=10.0, carbs=20.0, fat=5.0).total_calories
    assert row["calories"] == expected
    assert row["calories"] == 165.0
    assert row["logged_status"] == "partial"
    assert row["meal_count"] == 1


def test_two_meals_are_full_and_macros_sum_then_round():
    row = build_progress_day_row(
        date(2026, 9, 1),
        meals=[_meal(10.0, 20.0, 5.0), _meal(15.0, 10.0, 2.0)],
        burned_calories=0.0,
        hydration_ml=0,
        hydration_goal_ml=2000,
        protein_target_g=140.0,
        target_calories=1850.0,
        target_source="snapshot",
        is_cheat_day=True,
    )
    assert row["protein_g"] == 25.0
    assert row["logged_status"] == "full"
    assert row["is_cheat_day"] is True
    assert row["target_source"] == "snapshot"


def test_empty_day_is_none():
    row = build_progress_day_row(
        date(2026, 9, 1),
        meals=[],
        burned_calories=0.0,
        hydration_ml=0,
        hydration_goal_ml=2000,
        protein_target_g=140.0,
        target_calories=2000.0,
        target_source="base",
        is_cheat_day=False,
    )
    assert row["calories"] == 0.0
    assert row["logged_status"] == "none"
    assert row["meal_count"] == 0
