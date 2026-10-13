import pytest

from src.domain.services.chat.meal_request import is_meal_suggestion_request


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
