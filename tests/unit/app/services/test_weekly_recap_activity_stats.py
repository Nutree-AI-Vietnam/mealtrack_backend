"""Weekly recap numbers from workouts, drinks, weigh-ins, and the profile."""

from datetime import UTC, date, datetime, time, timedelta
from enum import Enum
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.app.services.weekly_recap_activity_stats import (
    recap_goal,
    strength_record,
    sugary_drinks,
    top_sugary_drink,
    weight_progress,
    workout_starts,
    workouts,
)
from src.domain.model.hydration.hydration_entry import HydrationEntry
from src.domain.model.movement.movement_entry import MovementEntry
from src.domain.model.weekly_recap import Workout
from src.domain.model.weight.weight_entry import WeightEntry

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
MONDAY = date(2026, 10, 5)
USER = "11111111-1111-1111-1111-111111111111"


def _local(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(MONDAY + timedelta(days=day), time(hour, minute), TZ)


def _move(
    day: int,
    hour: int,
    minute: int = 0,
    *,
    minutes: int = 45,
    kcal: float = 320.4,
    activity_id: str | None = "running",
) -> MovementEntry:
    return MovementEntry(
        user_id=USER,
        activity_name="Workout",
        duration_min=minutes,
        kcal_burned=kcal,
        intensity="moderate",
        logged_at=_local(day, hour, minute).astimezone(UTC),
        activity_id=activity_id,
    )


def _drink(
    sugar: float, carbs: float, name: str = "Trà sữa", drink_id: str | None = None
) -> HydrationEntry:
    return HydrationEntry(
        user_id=USER,
        drink_name_snapshot=name,
        drink_id=drink_id,
        volume_ml=500,
        credited_ml=400,
        logged_at=_local(2, 15).astimezone(UTC),
        carbs_g=carbs,
        sugar_g=sugar,
    )


def _weigh(day: int, kg: float) -> WeightEntry:
    return WeightEntry(user_id=USER, weight_kg=kg, recorded_at=_local(day, 7))


def _profile(**overrides) -> SimpleNamespace:
    values = {"weight_kg": 80.0, "target_weight_kg": 70.0, "goal_start_weight_kg": None}
    return SimpleNamespace(**(values | overrides))


class _Goal(Enum):
    BULK = "bulk"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("cut", "cut"),
        (" BULK ", "bulk"),
        (_Goal.BULK, "bulk"),
        ("recomp", "recomp"),
        ("maintain", "recomp"),
        (None, "recomp"),
    ],
)
def test_recap_goal(raw, expected):
    assert recap_goal(raw) == expected


def test_workouts_are_the_weeks_moves_in_local_time():
    entries = [
        _move(3, 18, activity_id="gym_strength", kcal=250.6),
        _move(0, 0, 30, minutes=30),  # Monday 00:30 local, Sunday in UTC
        _move(-1, 20),  # previous Sunday
        _move(7, 6),  # next Monday
        _move(2, 18, minutes=0),
    ]

    assert workouts(entries, MONDAY, TZ) == (
        Workout(day=0, minutes=30, kcal=320, strength=False),
        Workout(day=3, minutes=45, kcal=251, strength=True),
    )


def test_workout_starts_go_back_by_the_duration_and_skip_backdated_moves():
    entries = [_move(1, 18, 30, minutes=60), _move(4, 12)]

    assert workout_starts(entries, MONDAY, TZ) == [(1, _local(1, 17, 30))]


def test_strength_record_beats_every_earlier_week():
    strength = "gym_strength"
    earlier = [_move(day, 18, activity_id=strength) for day in (-14, -12)]
    this_week = [_move(day, 18, activity_id=strength) for day in (0, 2, 4)]
    running = [_move(day, 7) for day in (1, 3, 5, 6)]

    assert strength_record(earlier + this_week + running, MONDAY, TZ) is True
    assert strength_record(earlier + this_week[:2], MONDAY, TZ) is False


def test_strength_record_needs_an_earlier_week_to_beat():
    entries = [_move(day, 18, activity_id="gym_strength") for day in (0, 2)]

    assert strength_record(entries, MONDAY, TZ) is False


def test_sugary_drinks_counts_drinks_with_enough_sugar():
    drinks = [_drink(sugar=25, carbs=25), _drink(sugar=10, carbs=12), _drink(9, 30)]

    assert sugary_drinks(drinks, 10.0) == (2, 148)


def test_top_sugary_drink_is_the_one_behind_half_the_sugary_drinks():
    drinks = [
        _drink(25, 30, "Trà sữa  trân châu"),
        _drink(25, 30, "Trà sữa trân châu"),
        _drink(30, 35, "Coca-Cola"),
        _drink(2, 2, "Coca-Cola"),  # not sugary
        _drink(2, 2, "Coca-Cola"),
    ]

    assert top_sugary_drink(drinks, 10.0) == "Trà sữa trân châu"


def test_top_sugary_drink_needs_a_clear_favourite():
    drinks = [_drink(25, 30, name) for name in ("Trà sữa", "Coca-Cola", "Sinh tố")]

    assert top_sugary_drink(drinks, 10.0) is None
    assert top_sugary_drink([], 10.0) is None


def test_top_sugary_drink_is_no_favourite_on_an_even_split():
    drinks = [_drink(25, 30, name) for name in ("Trà sữa", "Trà sữa", "Soda", "Soda")]

    assert top_sugary_drink(drinks, 10.0) is None


def test_top_sugary_drink_counts_a_catalog_drink_by_id_under_its_catalog_name():
    # Older rows kept a localized label; the id still says it is milk tea.
    drinks = [
        _drink(40, 45, "Milk tea", "milk-tea"),
        _drink(40, 45, "Trà sữa", "milk-tea"),
        _drink(30, 35, "Coca-Cola"),
    ]

    assert top_sugary_drink(drinks, 10.0) == "Milk tea"


def test_weight_progress_from_the_weeks_average_weigh_in():
    progress, delta = weight_progress(
        "cut",
        _profile(),
        [_weigh(0, 78.4), _weigh(4, 77.6)],
        [_weigh(-3, 79.0)],
    )

    assert progress == pytest.approx(0.2)
    assert delta == pytest.approx(0.1)


def test_weight_progress_starts_from_the_goal_start_weight():
    progress, _ = weight_progress(
        "bulk",
        _profile(weight_kg=63.0, target_weight_kg=70.0, goal_start_weight_kg=60.0),
        [_weigh(2, 62.5)],
        [],
    )

    assert progress == pytest.approx(0.25)


@pytest.mark.parametrize(
    ("goal", "profile", "this_week", "last_week", "expected"),
    [
        ("recomp", _profile(), [_weigh(0, 78.0)], [], (None, None)),
        ("cut", None, [_weigh(0, 78.0)], [], (None, None)),
        ("cut", _profile(target_weight_kg=None), [_weigh(0, 78.0)], [], (None, None)),
        ("cut", _profile(), [], [_weigh(-2, 79.0)], (None, None)),
        ("cut", _profile(), [_weigh(0, 78.0)], [], (pytest.approx(0.2), None)),
    ],
)
def test_weight_progress_without_enough_to_go_on(
    goal, profile, this_week, last_week, expected
):
    assert weight_progress(goal, profile, this_week, last_week) == expected
