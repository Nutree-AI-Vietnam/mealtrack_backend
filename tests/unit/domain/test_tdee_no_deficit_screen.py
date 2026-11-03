"""Minors and underweight adults are not given a calorie deficit."""

from src.domain.model.user import Goal, JobType, Sex, TdeeRequest, UnitSystem
from src.domain.services.tdee_service import (
    TdeeCalculationService,
    deficit_not_allowed,
)


def _request(*, age: int, height: float, weight: float, goal: Goal) -> TdeeRequest:
    return TdeeRequest(
        age=age,
        sex=Sex.FEMALE,
        height=height,
        weight=weight,
        job_type=JobType.DESK,
        training_days_per_week=0,
        training_minutes_per_session=0,
        goal=goal,
        body_fat_pct=None,
        unit_system=UnitSystem.METRIC,
    )


def _calories(response) -> float:
    macros = response.macros
    return macros.protein * 4 + macros.carbs * 4 + macros.fat * 9


def test_screen_flags_minors_and_bmi_below_18_5():
    assert deficit_not_allowed(age=13, weight_kg=55, height_cm=155)
    assert deficit_not_allowed(age=30, weight_kg=46, height_cm=160)  # BMI 18.0
    assert not deficit_not_allowed(age=30, weight_kg=60, height_cm=160)


def test_teen_cut_plans_at_maintenance():
    service = TdeeCalculationService()
    cut = service.calculate_tdee(_request(age=13, height=155, weight=55, goal=Goal.CUT))
    maintain = service.calculate_tdee(
        _request(age=13, height=155, weight=55, goal=Goal.RECOMP)
    )

    assert abs(_calories(cut) - _calories(maintain)) < 1
    assert cut.pace_range is None


def test_underweight_cut_plans_at_maintenance():
    service = TdeeCalculationService()
    cut = service.calculate_tdee(_request(age=30, height=160, weight=46, goal=Goal.CUT))
    maintain = service.calculate_tdee(
        _request(age=30, height=160, weight=46, goal=Goal.RECOMP)
    )

    assert abs(_calories(cut) - _calories(maintain)) < 1


def test_adult_cut_still_gets_a_deficit():
    service = TdeeCalculationService()
    cut = service.calculate_tdee(_request(age=30, height=160, weight=70, goal=Goal.CUT))
    maintain = service.calculate_tdee(
        _request(age=30, height=160, weight=70, goal=Goal.RECOMP)
    )

    assert _calories(cut) < _calories(maintain) - 100
