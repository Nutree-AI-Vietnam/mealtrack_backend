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
        "What's left?",
        "Cite protein guidance",
        "",
    ],
)
def test_not_meal_refinements(text: str) -> None:
    assert not is_meal_refinement(text)
