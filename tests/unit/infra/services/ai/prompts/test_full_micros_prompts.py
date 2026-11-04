"""Scan prompts widen from ten micros keys to 21 only when asked."""

import pytest

from src.domain.model.ai.full_micros_contracts import FULL_MICROS_ADDED_KEYS
from src.domain.services.prompts.system_prompts import SystemPrompts
from src.domain.strategies.meal_analysis_strategy import (
    FoodLabelImageAnalysisStrategy,
)


def _prompts() -> dict[str, str]:
    return {
        "vision_en": SystemPrompts.get_vision_analysis_prompt("en"),
        "vision_vi": SystemPrompts.get_vision_analysis_prompt("vi"),
        "text": SystemPrompts.get_meal_text_parsing_prompt("vi"),
        "label": FoodLabelImageAnalysisStrategy().get_analysis_prompt(),
    }


@pytest.mark.parametrize("name", ["vision_en", "vision_vi", "text", "label"])
def test_every_scan_prompt_asks_for_every_new_key(name):
    prompt = _prompts()[name]
    widened = SystemPrompts.with_full_micros(prompt)
    assert widened != prompt
    for key in FULL_MICROS_ADDED_KEYS:
        assert key in widened
        assert key not in prompt


@pytest.mark.parametrize("name", ["vision_en", "text"])
def test_wide_prompts_keep_no_ten_key_wording(name):
    widened = SystemPrompts.with_full_micros(_prompts()[name])
    assert "ten micros" not in widened
    assert "all ten keys" not in widened
    assert "all 21" in widened


def test_todays_prompts_still_ask_for_ten():
    assert "all ten keys" in SystemPrompts.VISION_ANALYSIS
    assert "estimate all ten micros" in SystemPrompts.VISION_ANALYSIS


def test_a_prompt_without_the_ten_key_wording_fails_loudly():
    with pytest.raises(ValueError):
        SystemPrompts.with_full_micros(SystemPrompts.INGREDIENT_IDENTIFY)
