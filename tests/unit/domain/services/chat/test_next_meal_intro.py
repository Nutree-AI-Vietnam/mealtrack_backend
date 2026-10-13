from src.domain.services.chat.next_meal_intro import (
    cook_reply_already_said,
    eaten_today_name,
    next_meal_ack,
    next_meal_intro,
    next_meal_unavailable,
    verified_cook_reply,
    with_eaten_today,
)


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
        "Tap a card for the recipe, or log it from the card."
    )
    assert "420" not in text


def test_intro_uses_vietnamese_for_one_dinner() -> None:
    text = next_meal_intro(
        [{"name": "Cơm cá hồi", "calories": 410}],
        locale="vi",
        slot="dinner",
    )
    assert text == (
        "Cơm cá hồi hợp cho bữa tối. "
        "Chạm thẻ để xem công thức, hoặc ghi bữa ngay trên thẻ."
    )


def test_intro_says_what_changed() -> None:
    cards = [{"name": "Tofu bowl"}, {"name": "Egg rice"}]
    text = next_meal_intro(
        cards,
        locale="en",
        slot="lunch",
        user_text="Make the tofu higher protein",
    )
    assert text is not None
    assert text.startswith("These are the higher-protein options.")
    assert "Tofu bowl" in text
    assert "kcal" not in text

    generic = next_meal_intro(
        cards,
        locale="en",
        slot="lunch",
        user_text="What's for lunch?",
    )
    assert generic is not None
    assert not generic.startswith("These are")


def test_ack_names_the_slot_without_a_number() -> None:
    assert next_meal_ack(locale="en", slot="dinner") == (
        "I'm picking dinner that fits what's left."
    )
    assert "kcal" not in next_meal_ack(locale="vi", slot="breakfast")
    assert "bữa sáng" in next_meal_ack(locale="vi", slot="breakfast")


def test_cook_reply_copies_the_named_card_steps() -> None:
    cards = [
        {
            "name": "Tofu bowl",
            "recipe_steps": [
                {"instruction": "Press the tofu dry."},
                {"instruction": "Pan-fry until golden."},
            ],
        },
        {
            "name": "Egg rice bowl",
            "recipe_steps": [{"instruction": "Fry the rice."}],
        },
    ]
    text = verified_cook_reply("How do I cook the tofu?", cards, locale="en")
    assert text == (
        "How to make Tofu bowl:\n1. Press the tofu dry.\n2. Pan-fry until golden."
    )
    assert (
        verified_cook_reply("Why is lunch the better fit?", cards, locale="en") is None
    )
    assert verified_cook_reply("How do I cook pho?", cards, locale="en") is None
    assert cook_reply_already_said("Press the tofu dry, then rest it.", text or "")
    vi = verified_cook_reply("Cách nấu món này", cards[:1], locale="vi")
    assert vi is not None
    assert vi.startswith("Cách làm Tofu bowl:")


def test_unavailable_names_the_slot_and_invents_no_dish() -> None:
    text = next_meal_unavailable(locale="en", slot="lunch")
    assert text == (
        "I couldn't find a lunch that fits what's left. "
        "Try another meal, or tell me what to change."
    )
    assert "kcal" not in text
    snack = next_meal_unavailable(locale="en", slot="snack")
    assert snack.startswith("I couldn't find a snack")
    vi = next_meal_unavailable(locale="vi", slot="dinner")
    assert "bữa tối" in vi
    assert "kcal" not in vi


def test_intro_names_a_meal_already_logged_today() -> None:
    text = next_meal_intro(
        [
            {"name": "Tofu bowl", "calories": 380},
            {"name": "Yogurt cup"},
        ],
        locale="en",
        slot="lunch",
        eaten_today="Pho",
    )
    assert text is not None
    assert text.startswith("You already logged Pho today.")
    assert "Tofu bowl" in text
    assert "380" not in text
    assert "kcal" not in text


def test_intro_skips_a_logged_dish_that_is_already_a_card() -> None:
    meals = (
        {"local_date": "2026-10-13", "dish_name": "Pho"},
        {"local_date": "2026-10-12", "dish_name": "Old soup"},
        {"local_date": "2026-10-13", "dish_name": "Tofu bowl"},
    )
    assert (
        eaten_today_name(
            meals,
            local_date="2026-10-13",
            suggestion_names=["Tofu bowl", "Yogurt cup"],
        )
        == "Pho"
    )
    assert (
        eaten_today_name(
            ({"local_date": "2026-10-13", "dish_name": "Tofu bowl"},),
            local_date="2026-10-13",
            suggestion_names=["tofu bowl"],
        )
        is None
    )


def test_vietnamese_intro_names_the_logged_meal() -> None:
    text = next_meal_intro(
        [{"name": "Đậu hũ"}],
        locale="vi",
        slot="dinner",
        eaten_today="Phở",
        user_text="Đổi món nhiều đạm hơn",
    )
    assert text is not None
    assert "Đây là các món nhiều đạm hơn." in text
    assert "Hôm nay bạn đã ghi Phở." in text
    assert text.index("nhiều đạm") < text.index("Phở")


def test_usable_sentence_gains_today_logged_dish_when_it_omits_it() -> None:
    noted = with_eaten_today(
        "Egg rice bowl is 420 kcal.",
        "Pho",
        locale="en",
    )
    assert noted == "You already logged Pho today. Egg rice bowl is 420 kcal."
    assert (
        with_eaten_today(
            "You already logged Pho today. Egg rice bowl fits lunch.",
            "Pho",
            locale="en",
        )
        == "You already logged Pho today. Egg rice bowl fits lunch."
    )


def test_intro_is_absent_without_a_name() -> None:
    assert next_meal_intro([{"calories": 420}], locale="en", slot="lunch") is None
