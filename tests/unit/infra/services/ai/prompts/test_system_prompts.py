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
    assert "`lookup_name`: concise canonical English food identity" in prompt
    assert (
        "For prepared dishes: return diner-visible components of one serving" in prompt
    )
    assert (
        "For a list of ingredients/foods: return the listed foods one-for-one" in prompt
    )
    assert "For single foods: return one item" in prompt
    assert "Fallback shape for providers without native schema generation" in prompt
    assert "english_unit" in prompt
    assert "fiber_g" in prompt
