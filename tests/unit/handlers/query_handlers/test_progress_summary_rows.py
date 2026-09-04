"""Derived-calorie precision for progress summary day rows."""

from datetime import UTC, date, datetime

from src.app.handlers.query_handlers.progress_summary_rows import build_progress_day_row
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.nutrition import Nutrition
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.micros import Micros


def _meal(
    protein: float,
    carbs: float,
    fat: float,
    fiber: float = 0.0,
    meal_type: str | None = None,
) -> Meal:
    created = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    return Meal(
        meal_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        status=MealStatus.READY,
        created_at=created,
        image=None,
        dish_name="Test",
        nutrition=Nutrition(
            macros=Macros(protein=protein, carbs=carbs, fat=fat, fiber=fiber)
        ),
        ready_at=created,
        meal_type=meal_type,
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


def test_fiber_and_target_come_from_meals_and_calorie_budget():
    row = build_progress_day_row(
        date(2026, 9, 1),
        meals=[_meal(10.0, 20.0, 5.0, fiber=8.0)],
        burned_calories=0.0,
        hydration_ml=0,
        hydration_goal_ml=2000,
        protein_target_g=140.0,
        target_calories=2000.0,
        target_source="base",
        is_cheat_day=False,
    )
    assert row["fiber_g"] == 8.0
    assert row["fiber_target_g"] == 28.0


def test_drink_macros_add_when_no_legacy_hydration_meal():
    drinks = Macros(protein=20.0, carbs=8.0, fat=1.0, fiber=2.0)
    row = build_progress_day_row(
        date(2026, 9, 1),
        meals=[],
        burned_calories=0.0,
        hydration_ml=400,
        hydration_goal_ml=2000,
        protein_target_g=140.0,
        target_calories=2000.0,
        target_source="base",
        is_cheat_day=False,
        drink_macros=drinks,
    )
    assert row["protein_g"] == 20.0
    assert row["fiber_g"] == 2.0
    assert row["calories"] == drinks.total_calories
    assert row["meal_count"] == 1
    assert row["logged_status"] == "partial"


def test_legacy_hydration_meals_skip_drink_macros():
    row = build_progress_day_row(
        date(2026, 9, 1),
        meals=[_meal(5.0, 10.0, 0.0, fiber=1.0, meal_type="hydration")],
        burned_calories=0.0,
        hydration_ml=400,
        hydration_goal_ml=2000,
        protein_target_g=140.0,
        target_calories=2000.0,
        target_source="base",
        is_cheat_day=False,
        drink_macros=Macros(protein=20.0, carbs=8.0, fat=1.0, fiber=2.0),
    )
    assert row["protein_g"] == 5.0
    assert row["fiber_g"] == 1.0


def test_nrf_fields_stay_off_without_micro_coverage():
    row = build_progress_day_row(
        date(2026, 9, 1),
        meals=[_meal(10.0, 20.0, 5.0, fiber=8.0)],
        burned_calories=0.0,
        hydration_ml=0,
        hydration_goal_ml=2000,
        protein_target_g=140.0,
        target_calories=2000.0,
        target_source="base",
        is_cheat_day=False,
    )
    assert row["nrf_coverage"] == 0
    assert row["nrf_quality"] == 0.0


def test_nrf_quality_turns_on_with_four_logged_micros():
    created = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    meal = Meal(
        meal_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        status=MealStatus.READY,
        created_at=created,
        image=None,
        dish_name="Test",
        nutrition=Nutrition(
            macros=Macros(protein=50.0, carbs=20.0, fat=5.0, fiber=25.0),
            micros=Micros(
                vitamin_c=90,
                calcium=1300,
                iron=18,
                sodium=0,
            ),
        ),
        ready_at=created,
    )
    row = build_progress_day_row(
        date(2026, 9, 1),
        meals=[meal],
        burned_calories=0.0,
        hydration_ml=0,
        hydration_goal_ml=2000,
        protein_target_g=140.0,
        target_calories=2000.0,
        target_source="base",
        is_cheat_day=False,
    )
    assert row["nrf_coverage"] == 4
    assert row["nrf_quality"] == 100.0
    assert row["iron_mg"] == 18.0
    assert row["sodium_mg"] == 0.0
    assert row["potassium_mg"] is None
    assert row["added_sugar_g"] is None


def test_missing_micros_leave_highlight_amounts_none():
    row = build_progress_day_row(
        date(2026, 9, 1),
        meals=[_meal(10.0, 20.0, 5.0, fiber=8.0)],
        burned_calories=0.0,
        hydration_ml=0,
        hydration_goal_ml=2000,
        protein_target_g=140.0,
        target_calories=2000.0,
        target_source="base",
        is_cheat_day=False,
    )
    assert row["iron_mg"] is None
    assert row["potassium_mg"] is None
    assert row["sodium_mg"] is None
    assert row["added_sugar_g"] is None


def test_drink_micros_contribute_highlight_amounts():
    drinks = Macros(protein=20.0, carbs=8.0, fat=1.0, fiber=2.0)
    row = build_progress_day_row(
        date(2026, 9, 1),
        meals=[],
        burned_calories=0.0,
        hydration_ml=400,
        hydration_goal_ml=2000,
        protein_target_g=140.0,
        target_calories=2000.0,
        target_source="base",
        is_cheat_day=False,
        drink_macros=drinks,
        drink_micros=Micros(iron=9.0, potassium=2350.0),
    )
    assert row["iron_mg"] == 9.0
    assert row["potassium_mg"] == 2350.0
    assert row["sodium_mg"] is None
    assert row["added_sugar_g"] is None
