"""Asking a model for a week of dishes, with nothing real behind it."""

import pytest

from src.domain.services.prompts.prompt_template_manager import PromptTemplateManager
from src.domain.services.weekly_meal_planner.ai_dish import (
    DishRequest,
    generate_week_of_dishes,
    reference_block,
)

BUILDERS = {
    "build_names_prompt": PromptTemplateManager.build_discovery_prompt,
    "build_recipe_prompt": PromptTemplateManager.build_recipe_details_prompt,
}


def _recipe(name, *, protein=25.0, steps=None, extra_line=True):
    lines = [
        {
            "name": "chicken breast",
            "amount": 120,
            "unit": "g",
            "protein": protein,
            "carbs": 0,
            "fat": 3,
        }
    ]
    if extra_line:
        lines.append(
            {
                "name": "steamed greens",
                "amount": 120,
                "unit": "g",
                "protein": 3,
                "carbs": 12,
                "fat": 1,
                "fiber": 3,
            }
        )
    return {
        "name": name,
        "prep_time_minutes": 20,
        "ingredients": lines,
        "recipe_steps": steps
        or [{"instruction": "Grill for 12 minutes until cooked through."}],
    }


class _Model:
    """Answers names for a meal type, then a recipe for each name asked for."""

    def __init__(self, names, recipes=None, fail_on=()):
        self._names = list(names)
        self._recipes = recipes or {}
        self._fail_on = set(fail_on)
        self.prompts = []

    async def ask(self, prompt, system):
        self.prompts.append(prompt)
        if "different" in prompt and "meals, each" in prompt:
            return [{"name": name} for name in self._names]
        name = _requested_name(prompt)
        if name in self._fail_on:
            raise RuntimeError("the model is having a day")
        return self._recipes.get(name, _recipe(name))


def _requested_name(prompt):
    marker = 'Generate complete recipe for: "'
    start = prompt.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    return prompt[start : prompt.find('"', start)]


def _requests(count, meal_type="lunch", kcal=600):
    return tuple(
        DishRequest(
            day_index=day, slot_index=1, meal_type=meal_type, target_calories=kcal
        )
        for day in range(count)
    )


@pytest.mark.asyncio
async def test_every_slot_gets_its_own_dish():
    model = _Model(["Grilled chicken bowl", "Lemongrass pork", "Steamed fish plate"])

    week = await generate_week_of_dishes(
        _requests(3), ask=model.ask, new_id=lambda: "x", **BUILDERS
    )

    assert len(week.dishes) == 3
    assert week.unfilled == ()
    assert len({dish.name for dish in week.dishes.values()}) == 3


@pytest.mark.asyncio
async def test_a_model_that_repeats_itself_does_not_fill_the_week_with_one_meal():
    """The complaint this whole path exists to answer."""
    model = _Model(["Grilled chicken bowl"] * 3)

    week = await generate_week_of_dishes(
        _requests(3), ask=model.ask, new_id=lambda: "x", **BUILDERS
    )

    assert len(week.dishes) == 1
    assert len(week.unfilled) == 2
    assert any("ALREADY_IN_THE_WEEK" in dish.reasons for dish in week.rejected)


@pytest.mark.asyncio
async def test_an_unsafe_recipe_is_turned_away_and_the_slot_tries_again():
    unsafe = _recipe("Chicken salad", steps=[{"instruction": "Slice the raw chicken."}])
    model = _Model(
        ["Chicken salad", "Lemongrass pork"], recipes={"Chicken salad": unsafe}
    )

    week = await generate_week_of_dishes(
        _requests(1), ask=model.ask, new_id=lambda: "x", **BUILDERS
    )

    assert [dish.name for dish in week.dishes.values()] == ["Lemongrass pork"]
    assert week.rejected[0].name == "Chicken salad"


@pytest.mark.asyncio
async def test_a_call_that_raises_costs_one_slot_not_the_week():
    model = _Model(
        ["Grilled chicken bowl", "Lemongrass pork", "Steamed fish plate"],
        fail_on={"Grilled chicken bowl", "Lemongrass pork"},
    )

    week = await generate_week_of_dishes(
        _requests(2), ask=model.ask, new_id=lambda: "x", **BUILDERS
    )

    assert len(week.dishes) == 1
    assert len(week.unfilled) == 1


@pytest.mark.asyncio
async def test_the_catalogue_is_shown_as_a_hint_and_told_not_to_be_copied():
    model = _Model(["Grilled chicken bowl"])

    await generate_week_of_dishes(
        _requests(1),
        ask=model.ask,
        reference_dishes=("Thịt kho tàu", "Canh chua cá"),
        memory_text="Meals logged: 40\nCuisines eaten: Vietnamese",
        new_id=lambda: "x",
        **BUILDERS,
    )

    names_prompt = model.prompts[0]
    assert "Thịt kho tàu" in names_prompt
    assert "Do not reuse these names" in names_prompt
    assert "Cuisines eaten: Vietnamese" in names_prompt


class TestTheReferenceBlock:
    def test_nothing_to_show_says_nothing(self):
        assert reference_block(()) == ""
        assert reference_block(("", "   ")) == ""

    def test_a_long_catalogue_is_cut_down(self):
        block = reference_block(tuple(f"Dish {index}" for index in range(30)))

        assert "Dish 7" in block
        assert "Dish 8" not in block
