"""Gain-weight pace for a bulk goal: range, band, surplus and clamp."""

from types import SimpleNamespace

import pytest

from src.domain.constants import TDEEConstants
from src.domain.model.user import Goal, JobType, Sex, TdeeRequest, UnitSystem
from src.domain.services.meal_suggestion.suggestion_tdee_helpers import (
    build_tdee_request,
)
from src.domain.services.tdee_service import (
    TdeeCalculationService,
    allowed_gain_pace,
    allowed_pace,
    daily_pace_kcal,
    gain_pace_range,
)


def _bulk_request(**overrides) -> TdeeRequest:
    values = {
        "age": 30,
        "sex": Sex.MALE,
        "height": 175,
        "weight": 72,
        "body_fat_pct": None,
        "job_type": JobType.ON_FEET,
        "training_days_per_week": 0,
        "training_minutes_per_session": 0,
        "goal": Goal.BULK,
        "unit_system": UnitSystem.METRIC,
    }
    values.update(overrides)
    return TdeeRequest(**values)


def test_a_bulk_reports_the_gain_range_with_no_floor_cap():
    # Too small for a fast loss at a desk, but a surplus has no floor to hit.
    result = TdeeCalculationService().calculate_tdee(
        _bulk_request(sex=Sex.FEMALE, weight=50, height=155, job_type=JobType.DESK)
    )

    assert result.pace_range.min_kg_per_week == TDEEConstants.PACE_MIN_KG_PER_WEEK
    assert result.pace_range.max_kg_per_week == TDEEConstants.PACE_MAX_KG_PER_WEEK
    assert result.to_dict()["pace_range"]["max_kg_per_week"] == 1.0


@pytest.mark.parametrize(
    ("weight_kg", "band"),
    [(72, (0.25, 0.36)), (120, (0.3, 0.6)), (220, (0.55, 1.0)), (45, (0.25, 0.25))],
)
def test_recommended_gain_band_is_a_quarter_to_half_percent_within_limits(
    weight_kg, band
):
    paces = gain_pace_range(weight_kg)

    assert (paces.recommended_low_kg, paces.recommended_high_kg) == band


def test_a_gain_pace_sets_the_surplus_and_unset_keeps_the_flat_bulk():
    service = TdeeCalculationService()
    flat = service.calculate_tdee(_bulk_request())
    quarter = service.calculate_tdee(_bulk_request(weight_gain_kg_per_week=0.25))
    full = service.calculate_tdee(_bulk_request(weight_gain_kg_per_week=1.0))

    assert daily_pace_kcal(0.25) == pytest.approx(275)
    assert flat.macros.calories == pytest.approx(
        flat.tdee + TDEEConstants.BULKING_SURPLUS, abs=1
    )
    assert quarter.macros.calories == pytest.approx(quarter.tdee + 275, abs=1)
    assert full.macros.calories == pytest.approx(full.tdee + 1100, abs=1)


@pytest.mark.parametrize("goal", [Goal.CUT, Goal.RECOMP])
def test_a_gain_pace_moves_only_a_bulk_target(goal):
    service = TdeeCalculationService()
    plain = service.calculate_tdee(_bulk_request(goal=goal))
    paced = service.calculate_tdee(
        _bulk_request(goal=goal, weight_gain_kg_per_week=1.0)
    )

    assert paced.macros.calories == plain.macros.calories


def test_recomp_reports_no_pace_range():
    result = TdeeCalculationService().calculate_tdee(_bulk_request(goal=Goal.RECOMP))

    assert result.pace_range is None


def test_gain_clamp_caps_only_the_fast_end_and_only_for_a_bulk():
    assert allowed_gain_pace(_bulk_request(), 1.4) == 1.0
    assert allowed_gain_pace(_bulk_request(), 0.1) == 0.1
    assert allowed_gain_pace(_bulk_request(), 0.456) == 0.46
    assert allowed_gain_pace(_bulk_request(), None) is None
    assert allowed_gain_pace(_bulk_request(goal=Goal.CUT), 0.5) is None
    assert allowed_gain_pace(_bulk_request(goal=Goal.RECOMP), 0.5) is None
    # The bulk range must not let a loss pace be saved for a bulk goal.
    assert allowed_pace(_bulk_request(), 0.5) is None


def test_profile_requests_carry_the_saved_gain_pace():
    profile = SimpleNamespace(
        age=30,
        gender="male",
        height_cm=175,
        weight_kg=72,
        job_type="on_feet",
        training_days_per_week=0,
        training_minutes_per_session=0,
        training_level="beginner",
        fitness_goal="bulk",
        body_fat_percentage=None,
        dietary_preferences=[],
        weight_loss_kg_per_week=None,
        weight_gain_kg_per_week=0.5,
    )

    assert build_tdee_request(profile).weight_gain_kg_per_week == 0.5


# A gain pace is a clinician's call for under-18s and these conditions.


@pytest.mark.parametrize(
    "condition",
    ["pregnancy", "breastfeeding", "eating_disorder_history", "kidney_disease"],
)
def test_a_blocking_condition_keeps_the_flat_bulk_and_offers_no_range(condition):
    service = TdeeCalculationService()
    flat = service.calculate_tdee(_bulk_request(health_conditions=(condition,)))
    paced = service.calculate_tdee(
        _bulk_request(health_conditions=(condition,), weight_gain_kg_per_week=1.0)
    )

    assert paced.pace_range is None
    assert paced.gain_pace_blocked_reason == "health_condition"
    assert paced.to_dict()["gain_pace_blocked_reason"] == "health_condition"
    assert paced.macros.calories == flat.macros.calories
    assert flat.macros.calories == pytest.approx(
        flat.tdee + TDEEConstants.BULKING_SURPLUS, abs=1
    )


def test_an_under_18_keeps_the_flat_bulk_and_offers_no_range():
    result = TdeeCalculationService().calculate_tdee(
        _bulk_request(age=16, weight=60, height=170, weight_gain_kg_per_week=1.0)
    )

    assert result.pace_range is None
    assert result.gain_pace_blocked_reason == "minor"
    assert result.macros.calories == pytest.approx(
        result.tdee + TDEEConstants.BULKING_SURPLUS, abs=1
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"health_conditions": ("insulin_treated_diabetes",)},
        # BMI 16.3: gaining is the point, so the pace stays.
        {"weight": 50, "height": 175},
    ],
    ids=["insulin-treated-diabetes", "underweight-adult"],
)
def test_insulin_treated_diabetes_and_underweight_adults_keep_the_gain_pace(
    overrides,
):
    result = TdeeCalculationService().calculate_tdee(
        _bulk_request(weight_gain_kg_per_week=1.0, **overrides)
    )

    assert result.gain_pace_blocked_reason is None
    assert result.pace_range.max_kg_per_week == 1.0
    assert result.macros.calories == pytest.approx(result.tdee + 1100, abs=1)


def test_a_blocked_user_cannot_save_a_gain_pace():
    assert (
        allowed_gain_pace(_bulk_request(health_conditions=("pregnancy",)), 0.5) is None
    )
    assert allowed_gain_pace(_bulk_request(age=16, weight=60, height=170), 0.5) is None
    assert (
        allowed_gain_pace(
            _bulk_request(health_conditions=("insulin_treated_diabetes",)), 0.5
        )
        == 0.5
    )


def test_a_cut_never_reports_a_gain_block():
    result = TdeeCalculationService().calculate_tdee(
        _bulk_request(goal=Goal.CUT, health_conditions=("pregnancy",))
    )

    assert result.gain_pace_blocked_reason is None
    assert result.deficit_blocked_reason == "health_condition"


def test_profile_requests_carry_the_saved_conditions():
    profile = SimpleNamespace(
        age=30,
        gender="female",
        height_cm=165,
        weight_kg=60,
        job_type="desk",
        training_days_per_week=0,
        training_minutes_per_session=0,
        training_level="beginner",
        fitness_goal="bulk",
        body_fat_percentage=None,
        dietary_preferences=[],
        health_conditions=["pregnancy"],
        weight_gain_kg_per_week=0.5,
    )

    assert build_tdee_request(profile).health_conditions == ("pregnancy",)
