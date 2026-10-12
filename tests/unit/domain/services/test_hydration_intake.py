"""Entry-only drinks count once, and a later log changes the day fingerprint."""

from datetime import UTC, datetime
from types import SimpleNamespace

from src.domain.model.meal import MealStatus
from src.domain.services.hydration_intake import (
    add_unrepresented_drink_totals,
    intake_watermark,
    unrepresented_hydration_entries,
)


def _meal(meal_id: str, protein: float, carbs: float, fat: float, fiber: float = 0):
    return SimpleNamespace(
        meal_id=meal_id,
        status=MealStatus.READY,
        nutrition=SimpleNamespace(
            macros=SimpleNamespace(protein=protein, carbs=carbs, fat=fat, fiber=fiber)
        ),
    )


def _entry(**kwargs):
    defaults = {
        "legacy_meal_id": None,
        "protein_g": 0,
        "carbs_g": 0,
        "fat_g": 0,
        "fiber_g": 0,
        "sugar_g": 0,
        "logged_at": datetime(2026, 10, 12, 1, 0, tzinfo=UTC),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_coffee_meal_does_not_hide_coconut_and_is_not_counted_twice():
    coffee_id = "c29c3dea-0b4e-44ec-b6c2-9c471512866e"
    meals = [
        _meal(coffee_id, 0, 0, 0.3),
        _meal("peanut", 7.7, 4.8, 14.8, 2.5),
        _meal("potato", 0.5, 6.2, 0.1, 0.9),
        _meal("banana-flower", 2.2, 16.5, 0.3, 6.9),
        _meal("anchovy", 9.2, 0, 1.9),
    ]
    entries = [
        _entry(legacy_meal_id=coffee_id, fat_g=0.3),
        _entry(carbs_g=9.6, fat_g=0.6, sugar_g=9.6),
    ]
    kept = unrepresented_hydration_entries(entries, meals)
    assert len(kept) == 1
    assert kept[0].carbs_g == 9.6

    meal_only = {
        "protein": 19.6,
        "carbs": 27.5,
        "fat": 17.4,
        "calories": 0,
    }
    combined = add_unrepresented_drink_totals(
        meal_only,
        meals,
        entries,
        user_timezone="Asia/Ho_Chi_Minh",
    )
    assert combined["protein"] == 19.6
    assert combined["carbs"] == 37.1
    assert round(combined["fat"], 1) == 18.0
    assert combined["calories"] > 0


def test_deleting_a_drink_changes_the_fingerprint_even_if_a_meal_is_newer():
    meal_touch = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)
    before = intake_watermark(
        meal_touch, datetime(2026, 10, 12, 4, 0, tzinfo=UTC), None
    )
    after = intake_watermark(meal_touch, None, None)
    assert before != after
    assert after == "2026-10-12T08:00:00+00:00||"
