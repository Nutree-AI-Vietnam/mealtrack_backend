def test_recipe_generation_prompt_exists():
    from src.domain.services.prompts.system_prompts import SystemPrompts

    assert hasattr(SystemPrompts, "RECIPE_GENERATION")
    assert isinstance(SystemPrompts.RECIPE_GENERATION, str)
    assert len(SystemPrompts.RECIPE_GENERATION) > 1000  # at least ~1024 tokens worth


def test_recipe_generation_has_worked_examples():
    from src.domain.services.prompts.system_prompts import SystemPrompts

    # Must have at least one worked example
    assert (
        "WORKED EXAMPLE" in SystemPrompts.RECIPE_GENERATION
        or "example" in SystemPrompts.RECIPE_GENERATION.lower()
    )
    # Must include the JSON structure
    assert "recipe_steps" in SystemPrompts.RECIPE_GENERATION
    assert "ingredients" in SystemPrompts.RECIPE_GENERATION


def test_meal_text_parsing_prompt_requires_localized_display_names():
    from src.domain.services.prompts.system_prompts import SystemPrompts

    prompt = SystemPrompts.get_meal_text_parsing_prompt("vi")

    assert "Vietnamese (vi)" in prompt
    assert "`lookup_name`: concise canonical English identity" in prompt
    assert "no minimum" in prompt
    assert "duplicate dish/components" in prompt
    assert "Preserve atomic foods and explicit lists one-for-one" in prompt
    assert "`micros`: all ten keys; null only if none can be estimated" in prompt
    assert "Fallback JSON shape (values are illustrative)" in prompt
    assert "Language changes display only, not nutrition" in prompt
    assert "Ready-to-eat dish weights are served weights" in prompt
    assert "english_unit" in prompt
    assert "fiber_g" in prompt


def test_active_meal_prompts_preserve_portion_micros_and_unknown_semantics():
    from src.domain.services.prompts.system_prompts import SystemPrompts

    expected_units = {
        "`vitamin_a` (mcg RAE)",
        "`vitamin_c`",
        "`vitamin_e`",
        "`calcium`",
        "`iron`",
        "`magnesium`",
        "`potassium`",
        "`sodium`",
        "`saturated_fat`",
        "`added_sugar`",
    }
    for prompt in (
        SystemPrompts.VISION_ANALYSIS,
        SystemPrompts.get_meal_text_parsing_prompt("en"),
    ):
        for key in expected_units:
            assert key in prompt
        assert "null for each genuinely unknown value" in prompt
        assert "zero only for supported absence" in prompt
        assert "per-100g profile × quantity_g / 100" in prompt
        assert "Carbs include fiber/sugar" in prompt
        assert "without systematic upward or downward bias" in prompt
        assert "no minimum" in prompt
        assert "their grams must sum to it" in prompt
        assert "Scale once" in prompt
        assert "Fat must be ≥" not in prompt

    assert (
        "2 to 5 primary constituent ingredients" not in SystemPrompts.MEAL_TEXT_PARSING
    )
    assert "Minimum 3 entries" not in SystemPrompts.VISION_ANALYSIS


def test_active_meal_prompt_examples_do_not_blanket_null_micronutrients():
    import json
    import re

    from src.domain.services.prompts.system_prompts import SystemPrompts

    expected_keys = {
        "vitamin_a",
        "vitamin_c",
        "vitamin_e",
        "calcium",
        "iron",
        "magnesium",
        "potassium",
        "sodium",
        "saturated_fat",
        "added_sugar",
    }
    for prompt in (
        SystemPrompts.VISION_ANALYSIS,
        SystemPrompts.get_meal_text_parsing_prompt("en"),
    ):
        examples = re.findall(r'"micros":\s*(\{[^{}]*\})', prompt)
        assert examples
        for example in examples:
            micros = json.loads(example)
            assert set(micros) == expected_keys
            assert any(value is not None for value in micros.values())


def test_vision_localization_builder_preserves_exact_language_contract():
    from src.domain.services.prompts.system_prompts import SystemPrompts

    for language in ("vi", "es", "fr", "de", "ja", "zh"):
        prompt = SystemPrompts.get_vision_analysis_prompt(language)
        assert f'"localized_language": "{language}"' in prompt
        assert '"localized_dish_name":' in prompt
        assert '"localized_name":' in prompt
        assert f"MUST be exactly `{language}`" in prompt
        assert (
            "Return only valid JSON matching the response schema and localized fields."
            in prompt
        )
        assert "Never change quantities or nutrition because of localization." in prompt
