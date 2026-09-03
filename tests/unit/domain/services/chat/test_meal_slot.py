import pytest

from src.domain.services.chat.meal_slot import (
    meal_portion_type_for_slot,
    resolve_meal_slot,
    slot_from_user_text,
    suggested_meal_slot,
)


@pytest.mark.parametrize(
    ("hour", "minute", "slot"),
    [
        (4, 59, "snack"),
        (5, 0, "breakfast"),
        (10, 29, "breakfast"),
        (10, 30, "lunch"),
        (14, 29, "lunch"),
        (14, 30, "snack"),
        (16, 59, "snack"),
        (17, 0, "dinner"),
        (21, 59, "dinner"),
        (22, 0, "snack"),
        (0, 0, "snack"),
    ],
)
def test_suggested_meal_slot_windows(hour: int, minute: int, slot: str) -> None:
    assert suggested_meal_slot(hour, minute) == slot


def test_portion_type_follows_slot() -> None:
    assert meal_portion_type_for_slot("snack") == "snack"
    assert meal_portion_type_for_slot("breakfast") == "main"
    assert meal_portion_type_for_slot("lunch") == "main"
    assert meal_portion_type_for_slot("dinner") == "main"


def test_user_text_overrides_clock_slot() -> None:
    assert slot_from_user_text("I want dinner tonight") == "dinner"
    assert slot_from_user_text("More breakfast ideas") == "breakfast"
    assert slot_from_user_text("bữa trưa nhanh") == "lunch"
    assert slot_from_user_text("breakfast then dinner") == "dinner"
    assert slot_from_user_text("just macros") is None
    assert resolve_meal_slot("breakfast", "I want dinner") == "dinner"
    assert resolve_meal_slot("lunch", "what's next") == "lunch"


def test_invalid_clock_raises() -> None:
    with pytest.raises(ValueError):
        suggested_meal_slot(24, 0)
    with pytest.raises(ValueError):
        suggested_meal_slot(8, 60)
