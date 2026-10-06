from src.domain.services.weekly_meal_planner.meal_practicality import (
    practicality_rank,
)


def test_breakfast_prefers_eggs_over_salmon_porridge():
    eggs = practicality_rank("Trứng ốp la", "breakfast")
    salmon = practicality_rank("Cháo cá hồi rau mồng tơi", "breakfast")

    assert eggs < salmon
    assert eggs == (0, 0)
    assert salmon[0] == 2


def test_breakfast_rejects_a_rice_plate_and_a_clickbait_title():
    eggs = practicality_rank("Trứng ốp la", "breakfast")
    rice = practicality_rank("Cơm cá hồi kiểu Nhật", "breakfast")
    article = practicality_rank(
        "Bé sẽ mê tít và ăn trọn chén súp tôm khoai tây", "breakfast"
    )

    assert eggs < rice
    assert eggs < article


def test_toast_stays_a_simple_breakfast():
    assert practicality_rank("Bánh mì trứng ốp la", "breakfast") == (0, 0)


def test_lunch_prefers_everyday_chicken_over_salmon():
    chicken = practicality_rank("Cơm gà xối mỡ", "lunch", ingredient_count=6)
    salmon = practicality_rank("Cơm cá hồi kiểu Nhật", "lunch", ingredient_count=8)

    assert chicken < salmon
    assert chicken[0] == 0


def test_long_recipes_sort_after_a_short_everyday_meal():
    quick = practicality_rank("Canh rau ngót", "dinner", total_minutes=20)
    long = practicality_rank("Bò hầm rau củ", "dinner", total_minutes=90)

    assert quick < long
