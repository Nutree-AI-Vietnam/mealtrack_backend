from src.domain.services.movement_catalog_service import (
    get_activity,
    get_all_activities,
    get_met,
    localized_activity_name,
    localized_activity_name_for_snapshot,
)


def test_catalog_contains_badminton_with_localized_names_and_met_values():
    activities = get_all_activities()

    badminton = next(item for item in activities if item["id"] == "badminton")

    assert badminton["name"]["en"] == "Badminton"
    assert badminton["name"]["vi"] == "Cầu lông"
    assert badminton["met"]["moderate"] == 7.0
    assert badminton["apple_health_type"] == "badminton"


def test_lookup_returns_none_for_unknown_activity():
    assert get_activity("unknown") is None


def test_get_met_returns_intensity_value_or_none():
    assert get_met("walking", "moderate") == 3.8
    assert get_met("badminton", "very_hard") is None


def test_localized_activity_name_walking_japanese_is_not_english():
    assert localized_activity_name("walking", "ja") == "ウォーキング"
    assert localized_activity_name("walking", "ja") != "Walking"


def test_localized_activity_name_for_snapshot_resolves_vietnamese_title():
    assert localized_activity_name_for_snapshot("Đi bộ", "ja") == "ウォーキング"
