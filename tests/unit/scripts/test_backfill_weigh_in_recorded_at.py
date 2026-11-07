"""Moving weigh-ins stored with the phone's wall clock back to their moment."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "backfill_weigh_in_recorded_at.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "backfill_weigh_in_recorded_at", _SCRIPT_PATH
)
assert _SPEC is not None
backfill = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(backfill)

CREATED = datetime(2026, 10, 26, 13, 18, 41, 602736)  # naive UTC, as stored


def _at(hours: float, seconds: float = -0.4) -> datetime:
    return (CREATED + timedelta(hours=hours, seconds=seconds)).replace(tzinfo=UTC)


def test_vietnam_wall_clock_is_seven_hours_ahead():
    assert backfill.wall_clock_offset(_at(7), CREATED) == timedelta(hours=7)


def test_offsets_west_of_utc_and_half_hours_count():
    assert backfill.wall_clock_offset(_at(-5), CREATED) == timedelta(hours=-5)
    assert backfill.wall_clock_offset(_at(5.5), CREATED) == timedelta(hours=5.5)


def test_a_correct_row_is_left_alone():
    assert backfill.wall_clock_offset(_at(0), CREATED) is None


def test_a_row_queued_offline_is_left_alone():
    assert backfill.wall_clock_offset(_at(-30), CREATED) is None
    assert backfill.wall_clock_offset(_at(7, seconds=-1200), CREATED) is None


def test_planned_shifts_move_each_row_back_by_its_offset():
    rows = [
        ("a", "u1", _at(7), CREATED),
        ("b", "u2", _at(0), CREATED),
        ("c", "u3", _at(-5), CREATED),
    ]
    assert backfill.planned_shifts(rows) == [
        ("a", _at(7), _at(0)),
        ("c", _at(-5), _at(0)),
    ]


def test_a_shift_onto_a_taken_time_is_skipped():
    rows = [("a", "u1", _at(7), CREATED), ("b", "u1", _at(0), CREATED)]
    assert backfill.planned_shifts(rows) == []
