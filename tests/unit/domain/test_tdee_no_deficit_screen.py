"""Minors and underweight adults are not given a calorie deficit."""

from src.domain.model.user import Goal, JobType, Sex, TdeeRequest, UnitSystem
from src.domain.services.tdee_service import (
    TdeeCalculationService,
    deficit_not_allowed,
)


def _request(
    *,
    age: int,
    height: float,
    weight: float,
    goal: Goal,
    health_conditions: tuple[str, ...] = (),
) -> TdeeRequest:
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
        health_conditions=health_conditions,
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


def test_pregnancy_and_a_history_of_disordered_eating_block_a_deficit():
    for condition in ("pregnancy", "breastfeeding", "eating_disorder_history"):
        assert deficit_not_allowed(
            age=30, weight_kg=70, height_cm=165, health_conditions=[condition]
        ), condition


def test_a_reported_condition_the_targets_ignore_changes_nothing():
    assert not deficit_not_allowed(
        age=30, weight_kg=70, height_cm=165, health_conditions=["kidney_disease"]
    )


def test_kidney_disease_caps_protein_near_the_kdigo_suggestion():
    service = TdeeCalculationService()
    plain = service.calculate_tdee(
        _request(age=30, height=160, weight=60, goal=Goal.CUT)
    )
    ckd = service.calculate_tdee(
        _request(
            age=30,
            height=160,
            weight=60,
            goal=Goal.CUT,
            health_conditions=("kidney_disease",),
        )
    )

    # 1.8 g/kg down to 0.8 g/kg for a 60 kg user.
    assert plain.macros.protein == 108.0
    assert ckd.macros.protein == 48.0


def test_a_pregnant_user_plans_at_maintenance():
    service = TdeeCalculationService()
    cut = service.calculate_tdee(
        _request(
            age=30,
            height=160,
            weight=70,
            goal=Goal.CUT,
            health_conditions=("pregnancy",),
        )
    )
    maintain = service.calculate_tdee(
        _request(age=30, height=160, weight=70, goal=Goal.RECOMP)
    )

    assert abs(_calories(cut) - _calories(maintain)) < 1
    assert cut.pace_range is None


def test_the_response_says_why_a_cut_was_planned_at_maintenance():
    """The client needs a reason: the goal field still says "cut"."""
    service = TdeeCalculationService()
    cases = (
        ("minor", _request(age=13, height=155, weight=55, goal=Goal.CUT)),
        ("underweight", _request(age=30, height=160, weight=46, goal=Goal.CUT)),
        (
            "health_condition",
            _request(
                age=30,
                height=160,
                weight=70,
                goal=Goal.CUT,
                health_conditions=("pregnancy",),
            ),
        ),
    )
    for reason, request in cases:
        response = service.calculate_tdee(request)
        assert response.deficit_blocked_reason == reason, reason
        assert response.goal == Goal.CUT
        assert response.to_dict()["deficit_blocked_reason"] == reason


def test_a_plan_that_keeps_its_deficit_carries_no_reason():
    service = TdeeCalculationService()
    cut = service.calculate_tdee(_request(age=30, height=160, weight=70, goal=Goal.CUT))
    maintain = service.calculate_tdee(
        _request(age=30, height=160, weight=70, goal=Goal.RECOMP)
    )

    assert cut.deficit_blocked_reason is None
    assert maintain.deficit_blocked_reason is None
    assert "deficit_blocked_reason" not in cut.to_dict()
