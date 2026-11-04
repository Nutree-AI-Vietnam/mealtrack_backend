import unicodedata

import pytest

from src.domain.services.weekly_meal_planner.grocery_projection import is_pantry_staple


@pytest.mark.parametrize(
    "name",
    [
        "Muối",
        "muối hột",
        "Đường cát",
        "Nước mắm",
        "Tiêu xay",
        "Hạt tiêu",
        "Dầu ăn",
        "Dầu hào",
        "Hạt nêm",
        "Bột ngọt",
        "Nước tương",
        "Tỏi băm",
        "Hành tím",
        "Ớt",
        "Gạo",
        "Nước",
        "Cơm trắng",
        "Salt",
        "sea salt",
        "Brown sugar",
        "Black pepper",
        "Fish sauce",
        "Olive oil",
        "Rice vinegar",
        "Garlic",
        "Shallots",
        "Cornstarch",
        "Water",
        "Pepper",
        "Salt (to taste)",
        "Garlic, minced",
        "Muối, tiêu",
    ],
)
def test_seasonings_and_kitchen_staples_are_staples(name):
    assert is_pantry_staple(name)


@pytest.mark.parametrize(
    "name",
    [
        "Thịt heo",
        "Cà chua",
        "Đậu hũ",
        "Đậu phộng",
        "Ớt chuông",
        "Tỏi tây",
        "Gạo nếp",
        "Dầu cá",
        "Nước dừa",
        "Hành lá",
        "Hành tây",
        "Bí đỏ",
        "Chicken breast",
        "Bell pepper",
        "Fish oil",
        "Garlic chives",
        "Coconut water",
        "Rice noodles",
        "Sugar snap peas",
        "",
        "   ",
    ],
)
def test_fresh_food_is_not_a_staple(name):
    assert not is_pantry_staple(name)


def test_matching_keeps_accents_and_ignores_unicode_form():
    # "Dầu" is oil; "Đậu" is beans. Decomposed input still reads as "dầu".
    assert is_pantry_staple(unicodedata.normalize("NFD", "Dầu ăn"))
    assert not is_pantry_staple("Đậu xanh")
