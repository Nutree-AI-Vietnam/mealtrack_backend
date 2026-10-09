import inspect


def test_all_5_strategies_return_vision_analysis_system_prompt():
    """All 5 meal analysis strategies must return SystemPrompts.VISION_ANALYSIS."""
    from src.domain.services.prompts.system_prompts import SystemPrompts
    from src.domain.strategies.meal_analysis_strategy import (
        BasicAnalysisStrategy,
        IngredientAwareAnalysisStrategy,
        PortionAwareAnalysisStrategy,
        UserContextAwareAnalysisStrategy,
        WeightAwareAnalysisStrategy,
    )

    strategies = [
        BasicAnalysisStrategy(),
        PortionAwareAnalysisStrategy(350.0, "g"),
        IngredientAwareAnalysisStrategy(
            [{"name": "rice", "quantity": 200, "unit": "g"}]
        ),
        WeightAwareAnalysisStrategy(350.0),
        UserContextAwareAnalysisStrategy("I'm eating a chicken rice bowl"),
    ]
    for s in strategies:
        result = s.get_analysis_prompt()
        assert (
            result == SystemPrompts.VISION_ANALYSIS
        ), f"{s.__class__.__name__}.get_analysis_prompt() must return SystemPrompts.VISION_ANALYSIS"


def test_portion_aware_user_message_contains_portion_info():
    """PortionAwareAnalysisStrategy user message must include the portion size."""
    from src.domain.strategies.meal_analysis_strategy import (
        PortionAwareAnalysisStrategy,
    )

    s = PortionAwareAnalysisStrategy(350.0, "g")
    msg = s.get_user_message()
    assert "350" in msg
    assert "g" in msg


def test_weight_aware_user_message_contains_weight():
    from src.domain.strategies.meal_analysis_strategy import WeightAwareAnalysisStrategy

    s = WeightAwareAnalysisStrategy(280.0)
    msg = s.get_user_message()
    assert "280" in msg


def test_scan_context_messages_scope_and_scale_supplied_amounts():
    from src.domain.strategies.meal_analysis_strategy import (
        IngredientAwareAnalysisStrategy,
        PortionAwareAnalysisStrategy,
        UserContextAwareAnalysisStrategy,
        WeightAwareAnalysisStrategy,
    )

    portion = PortionAwareAnalysisStrategy(350.0, "g").get_user_message()
    assert "full meal" in portion
    assert "quantities sum to that mass" in portion
    assert "every macro and micro total" in portion

    weight = WeightAwareAnalysisStrategy(280.0).get_user_message()
    assert "total edible consumed weight" in weight
    assert "quantities sum to this weight" in weight
    assert "every macro and micro total" in weight

    ingredients = IngredientAwareAnalysisStrategy(
        [{"name": "rice", "quantity": 200, "unit": "g"}]
    ).get_user_message()
    assert "Keep each listed food and amount once" in ingredients
    assert "do not duplicate the whole dish" in ingredients

    context = UserContextAwareAnalysisStrategy(
        "half the chicken, all the rice"
    ).get_user_message()
    assert "named foods" in context
    assert "whole-meal amount applies to the full meal" in context
    assert "do not infer unseen consumption" in context


def test_food_label_strategy_prompt_contains_multilingual_label_rules():
    from src.domain.strategies.meal_analysis_strategy import (
        FoodLabelImageAnalysisStrategy,
    )

    strategy = FoodLabelImageAnalysisStrategy(
        crop_metadata={"crop_strategy": "food_label_visible_frame_v1"}
    )
    prompt = strategy.get_analysis_prompt()
    user_message = strategy.get_user_message()

    assert "Return ONLY valid JSON" in prompt
    assert "Thai nutrition panel" in prompt
    assert "serving_size.display_text" in prompt
    assert "Scanned Food Label" in prompt
    assert "Do not use Daily Value percentages as gram values" in prompt
    assert "Do not require any pre-extracted text" in prompt
    assert "crop_strategy: food_label_visible_frame_v1" in user_message


def test_scan_decomposition_rules_not_in_strategy_module():
    """SCAN_DECOMPOSITION_RULES and BASIC_SCAN_DECOMPOSITION_RULES must be removed
    from meal_analysis_strategy.py — they live in prompt_constants.py now."""
    from src.domain.strategies import meal_analysis_strategy

    source = inspect.getsource(meal_analysis_strategy)
    assert "SCAN_DECOMPOSITION_RULES = " not in source
    assert "BASIC_SCAN_DECOMPOSITION_RULES = " not in source


def test_vision_decomposition_rules_importable():
    """VISION_DECOMPOSITION_RULES must be importable from prompt_constants."""
    from src.domain.services.prompts.prompt_constants import VISION_DECOMPOSITION_RULES

    assert (
        "DECOMPOSE" in VISION_DECOMPOSITION_RULES
        or "decompos" in VISION_DECOMPOSITION_RULES.lower()
    )
