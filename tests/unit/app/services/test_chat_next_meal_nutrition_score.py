from src.app.services.chat_next_meal_candidates import (
    _fallback_meals_for_slot,
    map_discover_meals,
)


def test_map_discover_meals_calculates_nrf_quality_and_coverage() -> None:
    meals = [
        {
            "id": "m1",
            "name": "Salmon and Quinoa",
            "calories": 450,
            "protein_g": 35.0,
            "carbs_g": 40.0,
            "fat_g": 15.0,
            "fiber_g": 6.0,
            "micros": {
                "vitamin_a": 500.0,
                "vitamin_c": 45.0,
                "calcium": 120.0,
                "iron": 3.0,
                "potassium": 600.0,
                "sodium": 250.0,
                "saturated_fat": 2.5,
            },
        }
    ]

    cards = map_discover_meals(meals, "lunch")
    assert len(cards) == 1
    card = cards[0]
    assert card["name"] == "Salmon and Quinoa"
    assert card["fiber_g"] == 6.0
    assert card["nrf_coverage"] == 7
    assert card["nrf_quality"] is not None
    assert isinstance(card["nrf_quality"], float)
    assert 0 <= card["nrf_quality"] <= 100
    assert card["micros"]["vitamin_a"] == 500.0
    assert card["micros"]["calcium"] == 120.0


def test_map_discover_meals_handles_missing_micros() -> None:
    meals = [
        {
            "id": "m2",
            "name": "Simple Rice",
            "calories": 200,
            "protein_g": 4.0,
            "carbs_g": 44.0,
            "fat_g": 1.0,
        }
    ]

    cards = map_discover_meals(meals, "snack")
    assert len(cards) == 1
    card = cards[0]
    assert card["nrf_coverage"] == 0
    assert card["nrf_quality"] is None
    assert "micros" not in card


def test_fallback_meals_have_fiber_and_micros() -> None:
    for slot in ("breakfast", "lunch", "dinner", "snack"):
        fallbacks = _fallback_meals_for_slot(slot, "en", 400)
        assert len(fallbacks) > 0
        for meal in fallbacks:
            assert "fiber_g" in meal
            assert meal["fiber_g"] > 0
            assert "micros" in meal
            assert isinstance(meal["micros"], dict)
            assert len(meal["micros"]) > 0

        # When mapped, fallback meals produce valid nrf_quality
        cards = map_discover_meals(fallbacks, slot)
        for card in cards:
            assert card["nrf_coverage"] >= 1
            assert card["nrf_quality"] is not None
            assert card["nrf_quality"] > 0
            assert card["micros"] is not None
