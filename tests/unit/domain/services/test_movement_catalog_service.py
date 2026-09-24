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


def test_catalog_contains_tennis_with_localized_names_and_met_values():
    tennis = get_activity("tennis")

    assert tennis is not None
    assert tennis["name"]["en"] == "Tennis"
    assert tennis["name"]["vi"] == "Quần vợt"
    assert tennis["met"] == {"light": 5.0, "moderate": 7.3, "hard": 8.0}
    assert tennis["apple_health_type"] == "tennis"
    assert localized_activity_name_for_snapshot("Quần vợt", "ja") == "テニス"


def test_catalog_contains_pickleball_and_basketball():
    pickleball = get_activity("pickleball")
    basketball = get_activity("basketball")

    assert pickleball is not None
    assert pickleball["met"] == {"light": 4.0, "moderate": 5.0, "hard": 6.5}
    assert pickleball["apple_health_type"] == "pickleball"
    assert localized_activity_name("pickleball", "ja") == "ピックルボール"

    assert basketball is not None
    assert basketball["name"]["vi"] == "Bóng rổ"
    assert basketball["met"] == {"light": 4.5, "moderate": 6.5, "hard": 8.0}
    assert basketball["apple_health_type"] == "basketball"
    assert localized_activity_name_for_snapshot("Bóng rổ", "zh") == "篮球"


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
