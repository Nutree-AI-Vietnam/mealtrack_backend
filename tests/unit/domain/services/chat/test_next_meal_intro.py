from src.domain.services.chat.next_meal_intro import next_meal_intro


def test_intro_names_the_other_meals_and_skips_calories() -> None:
    text = next_meal_intro(
        [
            {"name": "Egg rice bowl", "calories": 420},
            {"name": "Yogurt cup", "calories": 220},
            {"name": "Tofu scramble", "calories": 380},
        ],
        locale="en",
        slot="breakfast",
    )
    assert text == (
        "Egg rice bowl fits breakfast. "
        "You can also choose Yogurt cup and Tofu scramble. "
        "Tap a card to see the recipe and log it."
    )
    assert "420" not in text


def test_intro_uses_vietnamese_for_one_dinner() -> None:
    text = next_meal_intro(
        [{"name": "Cơm cá hồi", "calories": 410}],
        locale="vi",
        slot="dinner",
    )
    assert text == ("Cơm cá hồi hợp cho bữa tối. Chạm thẻ để xem công thức và ghi bữa.")


def test_intro_is_absent_without_a_name() -> None:
    assert next_meal_intro([{"calories": 420}], locale="en", slot="lunch") is None
