from src.app.services.parse_text_composition import (
    classify_parse_text_input,
    composition_retry_feedback,
)


def test_named_dish_is_classified_for_composition():
    assert classify_parse_text_input("Bánh mì thịt") == "dish"
    assert classify_parse_text_input("1 bánh mì thịt") == "dish"
    assert classify_parse_text_input("1 bowl pho") == "dish"
    assert classify_parse_text_input("cơm tấm") == "dish"
    assert classify_parse_text_input("1 beef wellington") == "dish"
    assert classify_parse_text_input("beef wellington") == "dish"
    assert classify_parse_text_input("1 tô phở bò tái nạm") == "dish"
    assert classify_parse_text_input("1 đĩa cơm tấm sườn bì chả") == "dish"


def test_listed_or_measured_foods_are_not_dishes():
    assert classify_parse_text_input("trứng, sữa, bánh mì") == "ingredient_list"
    assert (
        classify_parse_text_input(
            "1 chén cơm trắng, 150g ức gà luộc, 1 đĩa rau muống xào tỏi"
        )
        == "ingredient_list"
    )
    assert classify_parse_text_input("100g chicken breast") == "single_food"
    assert classify_parse_text_input("1 banana") == "single_food"
    assert classify_parse_text_input("thịt nướng") == "single_food"
    assert classify_parse_text_input("1 miếng sườn nướng") == "single_food"
    assert classify_parse_text_input("bánh flan") == "single_food"
    assert classify_parse_text_input("low fat yogurt") == "single_food"
    assert classify_parse_text_input("thêm 1 quả trứng ốp la") == "single_food"
    assert classify_parse_text_input("200g ức gà áp chảo") == "single_food"


def test_retry_feedback_only_when_a_dish_comes_back_as_one_row():
    assert composition_retry_feedback(
        "Bánh mì thịt",
        [{"name": "Pork sandwich", "lookup_name": "Vietnamese pork sandwich"}],
    )
    assert (
        composition_retry_feedback(
            "trứng, sữa",
            [{"name": "Trứng", "lookup_name": "Egg"}],
        )
        is None
    )
    assert (
        composition_retry_feedback(
            "Bánh mì thịt",
            [
                {"name": "Bánh mì", "lookup_name": "Baguette"},
                {"name": "Thịt", "lookup_name": "Pork"},
            ],
        )
        is None
    )
    assert (
        composition_retry_feedback(
            "100g chicken breast",
            [{"name": "Chicken breast", "lookup_name": "chicken breast"}],
        )
        is None
    )
