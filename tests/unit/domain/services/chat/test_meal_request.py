import pytest

from src.domain.services.chat.meal_request import (
    is_meal_refinement,
    is_meal_suggestion_request,
)


@pytest.mark.parametrize(
    "text",
    [
        "What should I eat next?",
        "What should I eat?",
        "What's for dinner?",
        "More breakfast ideas",
        "I want dinner tonight",
        "Tôi nên ăn gì tiếp?",
        "ăn gì tối nay",
        "an gi",
        "Gợi ý bữa nhẹ",
    ],
)
def test_meal_requests(text: str) -> None:
    assert is_meal_suggestion_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "What's left?",
        "Cite protein guidance",
        "How many calories should I eat?",
        "breakfast then dinner",
        "just macros",
        "",
    ],
)
def test_not_meal_requests(text: str) -> None:
    assert not is_meal_suggestion_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "Make the tofu higher protein",
        "Skip the rice",
        "Make it vegetarian",
        "Đổi món nhiều đạm hơn",
        "Bỏ cơm",
    ],
)
def test_meal_refinements(text: str) -> None:
    assert is_meal_refinement(text)


@pytest.mark.parametrize(
    "text",
    [
        "Why is lunch the better fit?",
        "How do I get more protein?",
        "Which of these is higher protein?",
        "Is the tofu higher protein?",
        "So sánh món nào nhiều đạm hơn?",
        "What's left?",
        "Cite protein guidance",
        "",
    ],
)
def test_not_meal_refinements(text: str) -> None:
    assert not is_meal_refinement(text)


def test_ingredient_question_names_a_vegetarian_dish() -> None:
    from src.domain.services.chat.meal_request import is_ingredient_request

    text = "What's in Vegetarian pad thai?"
    assert is_ingredient_request(text)
    assert not is_meal_refinement(text)
    assert is_ingredient_request("Có gì trong đậu phụ?")
    assert is_ingredient_request("What are the ingredients in the tofu?")
    assert is_meal_refinement("Make a vegetarian recipe")


def test_comparison_is_not_a_new_meal_request() -> None:
    from src.domain.services.chat.meal_request import is_meal_comparison

    assert is_meal_comparison("Which of these is higher protein?")
    assert is_meal_comparison("Is the tofu higher protein?")
    assert not is_meal_comparison("Make it higher protein")
    assert not is_meal_comparison("What's for dinner?")


@pytest.mark.parametrize(
    "text",
    [
        "Why is lunch the better fit?",
        "How do I cook the tofu?",
        "Can you explain why the tofu fits?",
        "Làm sao nấu đậu hũ?",
        "Cách nấu món này",
    ],
)
def test_meal_explanations(text: str) -> None:
    from src.domain.services.chat.meal_request import is_meal_explanation

    assert is_meal_explanation(text)
    assert not is_meal_refinement(text)


@pytest.mark.parametrize(
    "text",
    [
        "Make the tofu higher protein",
        "What's for dinner?",
        "What's left?",
        "",
    ],
)
def test_not_meal_explanations(text: str) -> None:
    from src.domain.services.chat.meal_request import is_meal_explanation

    assert not is_meal_explanation(text)
