"""Active kcal and preset minutes match the app's movement detail screen."""

import pytest

from src.domain.services.movement_catalog_service import (
    active_kcal,
    default_duration_min,
    get_all_activities,
    get_met,
)

# The app's durationChipsMinutes per activity (activity_catalog.dart on delivery).
APP_DURATION_CHIPS = {
    "walking": [10, 20, 30, 45, 60],
    "running": [10, 20, 30, 45, 60],
    "cycling": [15, 30, 45, 60, 90],
    "gym_strength": [30, 45, 60, 75, 90],
    "cardio_hiit": [10, 15, 20, 30, 45],
    "yoga_stretching": [10, 20, 30, 45, 60],
    "swimming": [20, 30, 45, 60, 90],
    "badminton": [30, 45, 60, 90, 120],
    "tennis": [30, 45, 60, 90, 120],
    "pickleball": [30, 45, 60, 90, 120],
    "football": [30, 45, 60, 90],
    "basketball": [30, 45, 60, 90],
    "volleyball": [30, 45, 60, 90],
}


@pytest.mark.parametrize(
    ("activity_id", "intensity", "weight_kg", "minutes", "expected"),
    [
        # (MET - 1) x 3.5 x kg x min / 200, as estimateActiveKcal computes it
        ("running", "moderate", 62, 30, 244.125),
        ("gym_strength", "moderate", 62, 45, 195.3),
        ("swimming", "hard", 75.5, 45, 416.19375),
        ("yoga_stretching", "light", 50, 20, 22.75),
    ],
)
def test_active_kcal_matches_the_app_formula(
    activity_id, intensity, weight_kg, minutes, expected
):
    met = get_met(activity_id, intensity)

    assert active_kcal(met, weight_kg, minutes) == pytest.approx(expected)


def test_active_kcal_is_unrounded_and_never_negative():
    assert active_kcal(8.5, 62, 31) == pytest.approx(7.5 * 3.5 * 62 * 31 / 200)
    assert active_kcal(0.5, 62, 30) == 0.0
    assert active_kcal(8.5, 0, 30) == 0.0
    assert active_kcal(8.5, 62, 0) == 0.0


def test_default_minutes_are_the_apps_middle_chip():
    catalog_ids = {activity["id"] for activity in get_all_activities()}

    assert set(APP_DURATION_CHIPS) == catalog_ids
    for activity_id, chips in APP_DURATION_CHIPS.items():
        assert default_duration_min(activity_id) == chips[len(chips) // 2]
    assert default_duration_min("leo_nui") is None
    assert default_duration_min(None) is None


def test_catalog_payload_does_not_carry_the_default_minutes():
    assert all("default_duration_min" not in a for a in get_all_activities())
