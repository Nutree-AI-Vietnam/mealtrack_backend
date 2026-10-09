"""Tests for prompt constants, including fallback meal name localization."""

import pytest

from src.domain.constants.languages import SUPPORTED_TRANSLATION_LANGUAGES
from src.domain.services.prompts.prompt_constants import (
    LANGUAGE_NAMES,
    get_fallback_meal_name,
)
from src.domain.strategies.meal_analysis_strategy import BasicAnalysisStrategy


class TestFallbackMealNames:
    """Test suite for get_fallback_meal_name function."""

    def test_get_fallback_meal_name_english_breakfast(self):
        """Test English breakfast fallback name."""
        assert get_fallback_meal_name("en", "breakfast", 1) == "Healthy Breakfast #1"

    def test_get_fallback_meal_name_english_lunch(self):
        """Test English lunch fallback name."""
        assert get_fallback_meal_name("en", "lunch", 2) == "Healthy Lunch #2"

    def test_get_fallback_meal_name_english_dinner(self):
        """Test English dinner fallback name."""
        assert get_fallback_meal_name("en", "dinner", 3) == "Healthy Dinner #3"

    def test_get_fallback_meal_name_english_snack(self):
        """Test English snack fallback name."""
        assert get_fallback_meal_name("en", "snack", 4) == "Healthy Snack #4"

    def test_get_fallback_meal_name_vietnamese_breakfast(self):
        """Test Vietnamese breakfast fallback name (now returns English, translated in Phase 3)."""
        # Fallback names now always return English (translation happens in Phase 3)
        assert get_fallback_meal_name("vi", "breakfast", 1) == "Healthy Breakfast #1"

    def test_get_fallback_meal_name_vietnamese_lunch(self):
        """Test Vietnamese lunch fallback name (now returns English, translated in Phase 3)."""
        assert get_fallback_meal_name("vi", "lunch", 2) == "Healthy Lunch #2"

    def test_get_fallback_meal_name_vietnamese_dinner(self):
        """Test Vietnamese dinner fallback name (now returns English, translated in Phase 3)."""
        assert get_fallback_meal_name("vi", "dinner", 3) == "Healthy Dinner #3"

    def test_get_fallback_meal_name_vietnamese_snack(self):
        """Test Vietnamese snack fallback name (now returns English, translated in Phase 3)."""
        assert get_fallback_meal_name("vi", "snack", 4) == "Healthy Snack #4"

    def test_get_fallback_meal_name_unknown_language_defaults_to_english(self):
        """Test unknown language defaults to English."""
        assert get_fallback_meal_name("xyz", "breakfast", 1) == "Healthy Breakfast #1"

    def test_get_fallback_meal_name_empty_language_defaults_to_english(self):
        """Test empty language string defaults to English."""
        assert get_fallback_meal_name("", "lunch", 1) == "Healthy Lunch #1"

    def test_get_fallback_meal_name_none_language_defaults_to_english(self):
        """Test None language defaults to English."""
        assert get_fallback_meal_name(None, "dinner", 1) == "Healthy Dinner #1"

    def test_get_fallback_meal_name_case_insensitive_language(self):
        """Test language code is case insensitive (all return English now)."""
        # All languages now return English (translation in Phase 3)
        assert get_fallback_meal_name("VI", "breakfast", 1) == "Healthy Breakfast #1"
        assert get_fallback_meal_name("En", "breakfast", 1) == "Healthy Breakfast #1"
        assert get_fallback_meal_name("VI", "LUNCH", 1) == "Healthy Lunch #1"

    def test_get_fallback_meal_name_unknown_meal_type(self):
        """Test unknown meal type uses generic format."""
        result = get_fallback_meal_name("en", "brunch", 1)
        assert result == "Healthy Brunch #1"

    def test_get_fallback_meal_name_different_indices(self):
        """Test different index values."""
        assert get_fallback_meal_name("en", "breakfast", 1) == "Healthy Breakfast #1"
        assert get_fallback_meal_name("en", "breakfast", 2) == "Healthy Breakfast #2"
        assert get_fallback_meal_name("en", "breakfast", 5) == "Healthy Breakfast #5"

    def test_get_fallback_meal_name_vietnamese_different_indices(self):
        """Test Vietnamese with different indices (all return English now)."""
        # All languages now return English (translation in Phase 3)
        assert get_fallback_meal_name("vi", "breakfast", 1) == "Healthy Breakfast #1"
        assert get_fallback_meal_name("vi", "breakfast", 2) == "Healthy Breakfast #2"
        assert get_fallback_meal_name("vi", "breakfast", 4) == "Healthy Breakfast #4"

    def test_language_names_includes_supported_languages(self):
        """Test LANGUAGE_NAMES includes all supported languages."""
        assert "en" in LANGUAGE_NAMES
        assert "vi" in LANGUAGE_NAMES
        assert LANGUAGE_NAMES["en"] == "English"
        assert LANGUAGE_NAMES["vi"] == "Vietnamese"


class TestBasicAnalysisPromptConstraints:
    """Test prompt constraints for basic analysis strategy."""

    def test_basic_analysis_prompt_requires_json_schema_and_item_limit(self):
        """Keep output JSON-only and within the vision food-item limit."""

        prompt = BasicAnalysisStrategy().get_analysis_prompt().lower()

        assert "return only json matching the existing vision response schema" in prompt
        assert "up to the existing 8-item limit" in prompt

    def test_basic_analysis_prompt_returns_vision_analysis(self):
        """BasicAnalysisStrategy must return SystemPrompts.VISION_ANALYSIS."""
        from src.domain.services.prompts.system_prompts import SystemPrompts

        prompt = BasicAnalysisStrategy().get_analysis_prompt()
        assert prompt == SystemPrompts.VISION_ANALYSIS

    def test_localized_analysis_prompt_keeps_english_canonical_fields(self):
        prompt = BasicAnalysisStrategy(language="vi").get_analysis_prompt()

        assert '"localized_language": "vi"' in prompt
        assert '"localized_dish_name"' in prompt
        assert '"localized_name"' in prompt
        assert "canonical English food identities" in prompt
        assert "Vietnamese" in prompt

    @pytest.mark.parametrize(
        "language", sorted(SUPPORTED_TRANSLATION_LANGUAGES - {"en"})
    )
    def test_localized_analysis_prompt_covers_every_supported_language(self, language):
        prompt = BasicAnalysisStrategy(language=language).get_analysis_prompt()

        assert f'"localized_language": "{language}"' in prompt
        assert LANGUAGE_NAMES[language] in prompt
        assert "canonical English food identities" in prompt

    def test_basic_analysis_prompt_includes_food_guard_contract(self):
        """Vision prompt must expose the single-stage food guard field."""
        prompt = BasicAnalysisStrategy().get_analysis_prompt().lower()

        assert '"is_food"' in prompt
        assert "if no edible or drinkable item is visible" in prompt
        assert "do not invent" in prompt

    def test_basic_analysis_prompt_accepts_ambiguous_bakery_foods(self):
        """Bakery/display-case foods should not be rejected as non-food."""
        prompt = BasicAnalysisStrategy().get_analysis_prompt().lower()

        assert "plausible pastries" in prompt
        assert "display-case foods" in prompt
        assert "partially cropped foods" in prompt
        assert "behind glass" in prompt
        assert "lower confidence" in prompt

    def test_basic_analysis_prompt_treats_drinks_as_normal_food_items(self):
        """Meal scan should not route packaged beverages into hydration metadata."""
        prompt = BasicAnalysisStrategy().get_analysis_prompt().lower()

        assert "edible or drinkable items" in prompt
        assert "visible edible or drinkable items" in prompt
        assert "represent drinks as ordinary food rows" in prompt
        assert "if the image shows a packaged drink" not in prompt
        assert '"is_food": false,\n  "dish_name": "coca-cola' not in prompt

    def test_basic_analysis_strategy_optimized_flag_always_returns_vision_analysis(
        self,
    ):
        """optimized_prompt_enabled flag is a no-op; always returns VISION_ANALYSIS."""
        from src.domain.services.prompts.system_prompts import SystemPrompts

        prompt_default = BasicAnalysisStrategy().get_analysis_prompt()
        prompt_disabled = BasicAnalysisStrategy(
            optimized_prompt_enabled=False
        ).get_analysis_prompt()

        assert prompt_default == SystemPrompts.VISION_ANALYSIS
        assert prompt_disabled == SystemPrompts.VISION_ANALYSIS
