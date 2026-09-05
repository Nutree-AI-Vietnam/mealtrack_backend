from src.domain.services.hydration_catalog_service import (
    find_by_id,
    localized_name,
    localized_name_for_catalog_name,
)


def test_localized_name_water_japanese_is_not_english():
    drink = find_by_id("water")
    assert drink is not None
    assert localized_name(drink, "ja") == "水"
    assert localized_name(drink, "ja") != "Water"


def test_localized_name_water_vietnamese_unchanged():
    drink = find_by_id("water")
    assert drink is not None
    assert localized_name(drink, "vi") == "Nước lọc"


def test_localized_name_for_catalog_name_resolves_vietnamese_snapshot():
    assert localized_name_for_catalog_name("Nước lọc", "ja") == "水"


def test_localized_name_for_catalog_name_prefers_drink_id():
    assert localized_name_for_catalog_name("Water", "ja", drink_id="water") == "水"
