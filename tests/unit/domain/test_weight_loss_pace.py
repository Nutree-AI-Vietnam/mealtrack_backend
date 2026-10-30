"""Pace range, the derived cut, and today's fresh base."""

from datetime import date

import pytest

from src.domain.constants import TDEEConstants
from src.domain.model.user import Goal, JobType, Sex, TdeeRequest, UnitSystem
from src.domain.model.weekly import WeeklyMacroBudget
from src.domain.services.tdee_service import (
    TdeeCalculationService,
    allowed_pace,
    clamp_pace,
    daily_deficit_kcal,
    kg_to_lb,
    pace_range,
)
from src.domain.services.weekly_budget_service import WeeklyBudgetService


def _cut_request(**overrides) -> TdeeRequest:
    values = {
        "age": 30,
        "sex": Sex.MALE,
        "height": 175,
        "weight": 72,
        "body_fat_pct": None,
        "job_type": JobType.ON_FEET,
        "training_days_per_week": 0,
        "training_minutes_per_session": 0,
        "goal": Goal.CUT,
        "unit_system": UnitSystem.METRIC,
    }
    values.update(overrides)
    return TdeeRequest(**values)


def test_a_kg_a_week_costs_about_1100_kcal_a_day():
    assert daily_deficit_kcal(1) == pytest.approx(1100)
    assert daily_deficit_kcal(0.5) == pytest.approx(550)
    assert kg_to_lb(0.59) == pytest.approx(1.3, abs=0.01)
    assert kg_to_lb(None) is None


@pytest.mark.parametrize(
    ("sex", "weight", "height", "job", "expected_max"),
    [
        (Sex.MALE, 72, 175, JobType.DESK, 0.3),
        (Sex.MALE, 72, 175, JobType.ON_FEET, 0.6),
        (Sex.MALE, 72, 175, JobType.PHYSICAL, 0.91),
        (Sex.FEMALE, 60, 163, JobType.DESK, 0.23),
    ],
)
def test_the_fast_end_is_the_bmr_cap_rounded_down(
    sex, weight, height, job, expected_max
):
    result = TdeeCalculationService().calculate_tdee(
        _cut_request(sex=sex, weight=weight, height=height, job_type=job)
    )
    assert result.pace_range.min_kg_per_week == TDEEConstants.PACE_MIN_KG_PER_WEEK
    assert result.pace_range.max_kg_per_week == expected_max


def test_the_fast_end_never_passes_one_kg():
    result = TdeeCalculationService().calculate_tdee(
        _cut_request(weight=120, height=190, job_type=JobType.PHYSICAL)
    )
    assert result.pace_range.max_kg_per_week == TDEEConstants.PACE_MAX_KG_PER_WEEK


def test_recommended_band_is_half_to_one_percent_within_the_limits():
    band = pace_range(72, 2400, 1700, Sex.MALE)
    assert (band.recommended_low_kg, band.recommended_high_kg) == (0.36, 0.72)
    heavy = pace_range(130, 3000, 2200, Sex.MALE)
    assert (heavy.recommended_low_kg, heavy.recommended_high_kg) == (0.65, 1.0)
    light = pace_range(45, 1600, 1150, Sex.FEMALE)
    assert (light.recommended_low_kg, light.recommended_high_kg) == (0.25, 0.45)


def test_no_room_for_a_deficit_gives_a_zero_maximum():
    assert pace_range(50, 1200, 1000, Sex.FEMALE).max_kg_per_week == 0


def test_only_a_cut_reports_a_pace_range():
    service = TdeeCalculationService()
    assert service.calculate_tdee(_cut_request(goal=Goal.BULK)).pace_range is None
    assert service.calculate_tdee(_cut_request(goal=Goal.RECOMP)).pace_range is None


def test_clamp_keeps_slow_paces_and_caps_fast_ones():
    allowed = pace_range(72, 2336.25, 1668.75, Sex.MALE)
    assert clamp_pace(0.9, allowed) == 0.6
    assert clamp_pace(0.2, allowed) == 0.2
    assert clamp_pace(None, allowed) is None
    assert clamp_pace(0.5, pace_range(50, 1200, 1000, Sex.FEMALE)) is None


def test_allowed_pace_caps_at_the_floor_and_ignores_other_goals():
    assert allowed_pace(_cut_request(job_type=JobType.DESK), 0.8) == 0.3
    assert allowed_pace(_cut_request(), 0.45) == 0.45
    assert allowed_pace(_cut_request(goal=Goal.BULK), 0.5) is None
    assert allowed_pace(_cut_request(), None) is None


def test_a_chosen_pace_sets_the_deficit_and_unset_keeps_the_flat_cut():
    service = TdeeCalculationService()
    flat = service.calculate_tdee(_cut_request())
    paced = service.calculate_tdee(_cut_request(weight_loss_kg_per_week=0.5))
    assert flat.macros.calories == pytest.approx(
        flat.tdee - TDEEConstants.CUTTING_DEFICIT, abs=1
    )
    assert paced.macros.calories == pytest.approx(paced.tdee - 550, abs=1)

    bulk = service.calculate_tdee(_cut_request(goal=Goal.BULK))
    paced_bulk = service.calculate_tdee(
        _cut_request(goal=Goal.BULK, weight_loss_kg_per_week=1.0)
    )
    assert paced_bulk.macros.calories == bulk.macros.calories


def test_a_pace_past_the_maximum_still_stops_at_the_floor():
    service = TdeeCalculationService()
    result = service.calculate_tdee(
        _cut_request(
            sex=Sex.FEMALE,
            weight=45,
            height=150,
            age=60,
            job_type=JobType.DESK,
            weight_loss_kg_per_week=1.0,
        )
    )
    assert result.macros.calories >= result.bmr
    assert result.macros.calories >= TDEEConstants.MIN_CALORIES_FEMALE


def test_pace_change_day_does_not_inherit_this_weeks_leftover():
    week_start = date(2026, 10, 12)
    wednesday = date(2026, 10, 14)
    budget = WeeklyMacroBudget(
        weekly_budget_id="budget-1",
        user_id="user-1",
        week_start_date=week_start,
        target_calories=14000,
        target_protein=700,
        target_carbs=1400,
        target_fat=490,
        consumed_calories=6000,
        consumed_protein=300,
        consumed_carbs=600,
        consumed_fat=200,
    )
    consumed = {"calories": 6000.0, "protein": 300.0, "carbs": 600.0, "fat": 200.0}
    shared = {
        "weekly_budget": budget,
        "week_start": week_start,
        "target_date": wednesday,
        "base_daily_cal": 2000,
        "base_daily_protein": 100,
        "base_daily_carbs": 200,
        "base_daily_fat": 70,
        "bmr": 1500,
        "cheat_dates": [],
        "logged_past_days": 2,
        "consumed_total": consumed,
        "consumed_before_today": consumed,
        "consumed_for_redistribution": consumed,
    }
    normal = WeeklyBudgetService._apply_effective_adjusted_policy(
        **shared, fresh_base=False
    )
    fresh = WeeklyBudgetService._apply_effective_adjusted_policy(
        **shared, fresh_base=True
    )
    assert fresh.adjusted.calories == pytest.approx(2000, abs=0.2)
    assert normal.adjusted.calories < fresh.adjusted.calories
