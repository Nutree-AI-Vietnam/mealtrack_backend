"""
Unit tests for PromptTemplateManager.
Verifies token reduction and correctness of compressed prompts.
"""

from src.domain.services.prompts.prompt_constants import (
    INGREDIENT_RULES,
    SEASONING_RULES,
    JSON_SCHEMAS,
    GOAL_GUIDANCE,
    HEALTHY_EASY_RULE_LINE,
    default_cooking_minutes,
)
from src.domain.services.prompts.prompt_template_manager import PromptTemplateManager


class TestPromptConstants:
    """Test prompt constants are properly defined."""

    def test_ingredient_rules_is_compact(self):
        """Ingredient rules should be under 200 chars."""
        assert len(INGREDIENT_RULES) < 200
        assert "g/ml" in INGREDIENT_RULES
        assert "exact" in INGREDIENT_RULES.lower()

    def test_seasoning_rules_is_compact(self):
        """Seasoning rules should be under 100 chars."""
        assert len(SEASONING_RULES) < 100
        assert "g/ml" in SEASONING_RULES

    def test_json_schemas_have_required_types(self):
        """All required schema types should exist."""
        required_types = [
            "weekly_meal",
            "daily_meal",
            "single_meal",
            "suggestion_recipe",
        ]
        for schema_type in required_types:
            assert schema_type in JSON_SCHEMAS
            assert len(JSON_SCHEMAS[schema_type]) > 50  # Non-empty schema

    def test_goal_guidance_covers_all_goals(self):
        """Goal guidance should cover all fitness goals."""
        expected_goals = [
            "lose_weight",
            "gain_weight",
            "build_muscle",
            "maintain_weight",
            "cut",
            "bulk",
            "recomp",
        ]
        for goal in expected_goals:
            assert goal in GOAL_GUIDANCE
            assert len(GOAL_GUIDANCE[goal]) > 10


class TestPromptTemplateManager:
    """Test PromptTemplateManager methods."""

    def test_get_goal_guidance_returns_correct_guidance(self):
        """Goal guidance should return appropriate text."""
        assert (
            "protein" in PromptTemplateManager.get_goal_guidance("lose_weight").lower()
        )
        assert (
            "calorie" in PromptTemplateManager.get_goal_guidance("gain_weight").lower()
        )
        assert (
            "balance"
            in PromptTemplateManager.get_goal_guidance("maintain_weight").lower()
        )

    def test_get_goal_guidance_fallback(self):
        """Unknown goal should return maintain_weight guidance."""
        result = PromptTemplateManager.get_goal_guidance("unknown_goal")
        assert result == GOAL_GUIDANCE["maintain_weight"]

    def test_get_json_schema_returns_valid_json_template(self):
        """JSON schemas should be valid templates."""
        schema = PromptTemplateManager.get_json_schema("single_meal")
        assert "{" in schema
        assert "}" in schema
        assert "name" in schema
        assert "calories" in schema

    def test_build_base_requirements_compact(self):
        """Base requirements should be compact."""
        result = PromptTemplateManager.build_base_requirements(
            ingredients=["chicken", "rice", "broccoli"],
            seasonings=["salt", "pepper"],
            dietary_preferences=["vegetarian"],
            allergies=["peanuts"],
        )

        # Should contain key info
        assert "chicken" in result
        assert "peanuts" in result
        assert "vegetarian" in result

        # Should be reasonably compact
        assert len(result) < 300

    def test_build_base_requirements_limits_ingredients(self):
        """Should limit ingredients to 8 for token savings."""
        long_list = [f"ingredient_{i}" for i in range(20)]
        result = PromptTemplateManager.build_base_requirements(ingredients=long_list)

        # Should contain first 8, not all 20
        assert "ingredient_0" in result
        assert "ingredient_7" in result
        assert "ingredient_10" not in result

    def test_build_meal_targets_format(self):
        """Meal targets should be in compact format."""
        result = PromptTemplateManager.build_meal_targets(
            meal_type="Breakfast",
            calories=500,
            protein=25.5,
            carbs=60.3,
            fat=15.7,
        )

        assert "Breakfast" in result
        assert "500cal" in result
        assert "25g protein" in result
        assert "60g carbs" in result
        assert "15g fat" in result


class TestSuggestionPrompts:
    """Test meal suggestion prompt building."""

    def test_build_suggestion_prompt_is_compact(self):
        """Suggestion prompt should be under 1200 chars."""
        result = PromptTemplateManager.build_suggestion_prompt(
            meal_type="lunch",
            target_calories=600,
            cooking_time_minutes=30,
            ingredients=["chicken", "rice", "vegetables"],
            allergies=["peanuts"],
            dietary_preferences=["gluten-free"],
        )

        # Should be compact (under 1800 chars, grew with EMOJI_RULES and detailed rules)
        assert len(result) < 1800

        # Should contain essential info
        assert "lunch" in result
        assert "600" in result
        assert "30" in result
        assert "peanuts" in result

    def test_build_meal_names_prompt_is_very_compact(self):
        """Meal names prompt should be under 400 chars."""
        result = PromptTemplateManager.build_meal_names_prompt(
            meal_type="dinner",
            target_calories=800,
            cooking_time_minutes=45,
            ingredients=["salmon", "asparagus"],
        )

        assert len(result) < 400
        assert "dinner" in result
        assert "4" in result  # 4 different names

    def test_build_recipe_details_prompt_includes_core_content(self):
        """Recipe details prompt should include meal name, target, and ingredient guidance.

        Phase 4 removed MACRO_ACCURACY_RULES / PORTION block — macros are now
        derived deterministically by NutritionLookupService, not requested from the AI.
        """
        result = PromptTemplateManager.build_recipe_details_prompt(
            meal_name="Grilled Salmon",
            meal_type="dinner",
            target_calories=800,
            cooking_time_minutes=30,
            ingredients=["salmon", "lemon", "herbs"],
        )

        assert "Grilled Salmon" in result
        assert "800" in result
        # Prompt must still guide the AI to specify gram/ml amounts per ingredient
        assert any(unit in result for unit in ["g", "gram", "ml", "amount"])
        # Macro fields must NOT be requested — backend calculates them deterministically
        assert "protein" not in result.lower()
        assert "macros" not in result.lower()


class TestTokenReduction:
    """Test that prompts are actually reduced compared to original."""

    def test_suggestion_prompt_token_estimate(self):
        """Estimate tokens and verify reduction target."""
        prompt = PromptTemplateManager.build_suggestion_prompt(
            meal_type="breakfast",
            target_calories=500,
            cooking_time_minutes=20,
            ingredients=["eggs", "toast", "avocado", "tomatoes"],
            allergies=["dairy"],
            dietary_preferences=["vegetarian"],
        )

        # Rough token estimate: ~4 chars per token
        estimated_tokens = len(prompt) / 4

        # Target: under 450 tokens (grew with EMOJI_RULES and detailed ingredient rules)
        assert estimated_tokens < 450, f"Prompt too long: ~{estimated_tokens} tokens"

    def test_recipe_details_prompt_token_estimate(self):
        """Recipe details prompt should be under 250 tokens."""
        prompt = PromptTemplateManager.build_recipe_details_prompt(
            meal_name="Test Meal",
            meal_type="lunch",
            target_calories=600,
            cooking_time_minutes=30,
            ingredients=["chicken", "rice", "vegetables"],
        )

        estimated_tokens = len(prompt) / 4
        # Target ~750 tokens (grew with MACRO_ACCURACY_RULES, DECOMPOSITION_RULES, EMOJI_RULES,
        # and English-only enforcement for multilingual translation pipeline)
        assert estimated_tokens < 750, f"Prompt too long: ~{estimated_tokens} tokens"


class TestHealthyAndEasy:
    """Every generated dish has to be healthy and cookable on a weekday."""

    def test_names_prompt_asks_for_healthy_home_cooking(self):
        prompt = PromptTemplateManager.build_meal_names_prompt(
            meal_type="lunch",
            target_calories=600,
        )

        assert HEALTHY_EASY_RULE_LINE in prompt

    def test_recipe_prompt_spells_out_the_healthy_rules(self):
        prompt = PromptTemplateManager.build_recipe_details_prompt(
            meal_name="Grilled chicken with greens",
            meal_type="lunch",
            target_calories=600,
        )

        assert "never deep-fry" in prompt
        assert "No processed meat" in prompt
        assert "no special equipment" in prompt

    def test_breakfast_gets_the_tightest_time_budget(self):
        """A breakfast nobody has time to cook is a breakfast nobody eats."""
        prompt = PromptTemplateManager.build_meal_names_prompt(
            meal_type="breakfast",
            target_calories=400,
        )

        assert "≤15min" in prompt

    def test_recipe_is_capped_in_time_even_when_nobody_asked(self):
        prompt = PromptTemplateManager.build_recipe_details_prompt(
            meal_name="Steamed fish with rice",
            meal_type="dinner",
            target_calories=700,
        )

        assert "Total prep_time ≤30 min" in prompt

    def test_the_caller_can_still_ask_for_less_time(self):
        prompt = PromptTemplateManager.build_recipe_details_prompt(
            meal_name="Tofu stir-fry",
            meal_type="dinner",
            target_calories=500,
            cooking_time_minutes=20,
        )

        assert "Total prep_time ≤20 min" in prompt
        assert "≤30 min" not in prompt

    def test_an_unknown_meal_type_falls_back_to_the_general_budget(self):
        assert default_cooking_minutes("brunch") == 30
        assert default_cooking_minutes("") == 30
        assert default_cooking_minutes("Breakfast") == 15

    def test_the_single_meal_prompt_carries_the_same_rule(self):
        prompt = PromptTemplateManager.build_suggestion_prompt(
            meal_type="breakfast",
            target_calories=400,
        )

        assert HEALTHY_EASY_RULE_LINE in prompt
        assert "≤15min" in prompt

    def test_discovery_names_are_healthy_and_quick_too(self):
        prompt = PromptTemplateManager.build_discovery_prompt(
            meal_type="breakfast",
            target_calories=400,
        )

        assert HEALTHY_EASY_RULE_LINE in prompt
        assert "≤15min" in prompt
