from src.domain.services.weekly_meal_planner.late_legacy_slot_shape import (
    is_late_legacy_two_slot_plan,
)


def _pairs(*slots: int) -> set[tuple[int, int]]:
    return {(day, slot) for day in range(7) for slot in slots}


def test_exact_draft_v1_lunch_dinner_plan_is_repairable():
    assert is_late_legacy_two_slot_plan(
        algorithm_version="v1",
        status="draft",
        coordinates=_pairs(0, 1),
    )


def test_other_plan_shapes_are_left_alone():
    assert not is_late_legacy_two_slot_plan(
        algorithm_version="v2",
        status="draft",
        coordinates=_pairs(0, 1),
    )
    assert not is_late_legacy_two_slot_plan(
        algorithm_version="v1",
        status="confirmed",
        coordinates=_pairs(0, 1),
    )
    assert not is_late_legacy_two_slot_plan(
        algorithm_version="v1",
        status="draft",
        coordinates=_pairs(0, 1, 2),
    )
    assert not is_late_legacy_two_slot_plan(
        algorithm_version="v1",
        status="draft",
        coordinates=_pairs(1, 2),
    )
