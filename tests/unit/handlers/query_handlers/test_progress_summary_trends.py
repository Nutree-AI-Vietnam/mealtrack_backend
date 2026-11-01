"""Trends-only fields on a progress summary day row."""

from datetime import UTC, date, datetime

import pytest

from src.app.handlers.query_handlers.progress_summary_rows import build_progress_day_row
from src.app.handlers.query_handlers.progress_summary_trends import (
    included_burn_by_day,
    trends_goal_fields,
)
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.movement import MovementEntry
from src.domain.model.nutrition import Nutrition
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.micros import Micros

DAY = date(2026, 10, 30)


def _meal(
    micros: Micros | None = None,
    meal_type: str = "lunch",
    protein: float = 20.0,
    fiber: float = 5.0,
    status: MealStatus = MealStatus.READY,
) -> Meal:
    created = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    return Meal(
        meal_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        status=status,
        created_at=created,
        image=None,
        dish_name="Test",
        nutrition=Nutrition(
            macros=Macros(protein=protein, carbs=30.0, fat=10.0, fiber=fiber),
            micros=micros,
        ),
        ready_at=created,
        meal_type=meal_type,
    )


def _movement(minutes: int, included: bool = True) -> MovementEntry:
    return MovementEntry(
        user_id="u1",
        activity_name="Running",
        duration_min=minutes,
        kcal_burned=150.0,
        intensity="hard",
        logged_at=datetime(2026, 10, 30, 6, 0, tzinfo=UTC),
        activity_id="running",
        include_in_balance=included,
    )


def _row(meals, movement=None, trends=True):
    return build_progress_day_row(
        DAY,
        meals=meals,
        burned_calories=0.0,
        hydration_ml=0,
        hydration_goal_ml=2000,
        protein_target_g=100.0,
        fiber_target_g=25.0,
        target_calories=1600.0,
        target_source="base",
        is_cheat_day=False,
        trends_movement=(movement or []) if trends else None,
    )


def test_counts_how_many_food_meals_reported_each_nutrient():
    meals = [
        _meal(Micros(iron=2.0, sodium=300.0)),
        _meal(Micros(iron=1.5)),
        _meal(None),
        _meal(None),
        _meal(Micros(sodium=500.0)),
    ]
    row = _row(meals)
    assert row["micro_meal_counts"] == {"iron": 2, "sodium": 2}
    assert row["micros"] == {"iron": 3.5, "sodium": 800.0}


def test_drinks_add_to_totals_but_not_to_meal_counts():
    row = build_progress_day_row(
        DAY,
        meals=[_meal(Micros(calcium=100.0))],
        burned_calories=0.0,
        hydration_ml=250,
        hydration_goal_ml=2000,
        protein_target_g=100.0,
        fiber_target_g=25.0,
        target_calories=1600.0,
        target_source="base",
        is_cheat_day=False,
        drink_macros=Macros(protein=8.0, carbs=12.0, fat=8.0),
        drink_micros=Micros(calcium=300.0),
        trends_movement=[],
    )
    assert row["micros"]["calcium"] == 400.0
    assert row["micro_meal_counts"] == {"calcium": 1}


def test_skips_hydration_rows_and_unfinished_meals():
    meals = [
        _meal(Micros(iron=1.0), meal_type="hydration"),
        _meal(Micros(iron=1.0), status=MealStatus.PROCESSING),
        _meal(Micros(iron=1.0), status=MealStatus.INACTIVE),
    ]
    row = _row(meals)
    assert row["micro_meal_counts"] == {}
    assert row["meal_quality_counts"] == {"good": 0, "fair": 0, "low": 0}


def test_meal_quality_bands_and_mean_per_meal_type():
    rich = Micros(iron=18.0, potassium=4700.0, calcium=1300.0)
    poor = Micros(sodium=2300.0, added_sugar=50.0)
    meals = [
        _meal(rich, "breakfast", protein=50.0, fiber=25.0),
        _meal(poor, "dinner", protein=0.0, fiber=0.0),
        _meal(None, "lunch"),
    ]
    row = _row(meals)
    assert row["meal_quality_counts"] == {"good": 1, "fair": 0, "low": 1}
    assert row["quality_by_meal_type"] == {"breakfast": 100.0, "dinner": 0.0}


def test_movement_fields_come_from_the_day_entries():
    row = _row([], movement=[_movement(30), _movement(15, included=False)])
    assert row["movement_minutes"] == 45
    assert row["movement_sessions"] == 2
    assert row["movement_load"] == 135.0
    assert row["movement_kinds"] == ["run"]


def test_without_trends_the_row_has_no_trends_fields():
    row = _row([_meal(Micros(iron=1.0))], trends=False)
    for key in ("micros", "micro_meal_counts", "meal_quality_counts", "movement_load"):
        assert key not in row


def test_included_burn_matches_the_default_path():
    by_day = {DAY: [_movement(30), _movement(15, included=False)]}
    assert included_burn_by_day(by_day) == {DAY: 150.0}


def test_goal_fields_for_a_woman_of_30_on_1600_kcal():
    profile = type(
        "Profile", (), {"gender": "female", "age": 30, "dietary_preferences": []}
    )()
    fields = trends_goal_fields(profile, 1600.0, 4)
    assert fields["goals_revision"] == 4
    assert len(fields["micro_goals"]) == 21
    assert fields["micro_goals"]["iron"] == pytest.approx(17.4)
    assert fields["micro_goals"]["calcium"] == 800
    assert fields["micro_goals"]["saturated_fat"] == 17.8
    assert fields["micro_upper_limits"]["vitamin_a"] == {
        "value": 2700.0,
        "scope": "preformed_only",
    }
    assert "potassium" not in fields["micro_upper_limits"]
