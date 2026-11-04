"""Asking a model for a week of dishes, with nothing real behind it."""

import pytest

from src.domain.services.prompts.prompt_template_manager import PromptTemplateManager
from src.domain.services.weekly_meal_planner.ai_dish import (
    DishRequest,
    generate_week_of_dishes,
    language_block,
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


class TestTheLanguageTheWeekIsWrittenIn:
    """Nothing downstream translates an invented dish, so the model must."""

    def test_vietnamese_is_asked_for_by_name(self):
        block = language_block("vi")

        assert "Vietnamese" in block
        assert "home cook" in block

    def test_the_pricing_key_stays_english_and_the_shown_name_does_not(self):
        """A Vietnamese name normalises to rubble, so it cannot be the key."""
        from src.domain.services.meal_suggestion.ingredient_name_normalizer import (
            normalize_food_name,
        )

        assert normalize_food_name("thịt gà") != "thịt gà"
        block = language_block("vi")
        assert 'ingredient\'s "name" in plain English' in block
        assert '"local_name" holding what that ingredient is called' in block

    def test_the_reader_is_shown_the_local_name_when_there_is_one(self):
        from src.domain.model.weekly_meal_planner.saved_recipe import (
            SavedRecipeIngredient,
        )

        line = SavedRecipeIngredient(name="fish sauce", display_name="nước mắm")
        assert line.shown_name == "nước mắm"
        assert line.name == "fish sauce"

    def test_a_line_with_no_local_name_is_shown_as_it_is(self):
        from src.domain.model.weekly_meal_planner.saved_recipe import (
            SavedRecipeIngredient,
        )

        assert SavedRecipeIngredient(name="fish sauce").shown_name == "fish sauce"
        assert (
            SavedRecipeIngredient(name="fish sauce", display_name="  ").shown_name
            == "fish sauce"
        )

    def test_the_local_name_survives_being_stored_and_read_back(self):
        from src.domain.model.weekly_meal_planner.saved_recipe import (
            SavedRecipeSnapshot,
        )
        from src.domain.services.weekly_meal_planner.ai_dish import (
            snapshot_from_ai_recipe,
        )

        snapshot = snapshot_from_ai_recipe(
            {
                "name": "Rice bowl",
                "ingredients": [
                    {
                        "name": "fish sauce",
                        "local_name": "nước mắm",
                        "amount": 10,
                        "unit": "g",
                    },
                    {
                        "name": "jasmine rice",
                        "local_name": "gạo",
                        "amount": 150,
                        "unit": "g",
                        "carbs": 45,
                    },
                ],
                "recipe_steps": [{"instruction": "Nấu cơm."}],
            },
            dish_id="ai-1",
            language="vi",
        )
        read_back = SavedRecipeSnapshot.from_dict(snapshot.to_dict())

        assert [line.shown_name for line in read_back.ingredients] == [
            "nước mắm",
            "gạo",
        ]
        assert [line.name for line in read_back.ingredients] == [
            "fish sauce",
            "jasmine rice",
        ]

    def test_english_and_the_unknown_ask_for_nothing(self):
        assert language_block("en") == ""
        assert language_block("en-US") == ""
        assert language_block(None) == ""
        assert language_block("  ") == ""
        assert language_block("xx") == ""

    @pytest.mark.asyncio
    async def test_both_calls_carry_the_language(self):
        model = _Model(["Gà nướng sả"])

        await generate_week_of_dishes(
            _requests(1),
            ask=model.ask,
            language="vi",
            new_id=lambda: "x",
            **BUILDERS,
        )

        assert len(model.prompts) == 2
        assert "Vietnamese" in model.prompts[1]

    @pytest.mark.asyncio
    async def test_a_week_with_no_language_asks_for_none(self):
        model = _Model(["Grilled chicken bowl"])

        await generate_week_of_dishes(
            _requests(1), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        assert not any("Write the dish name" in prompt for prompt in model.prompts)


class TestTheCookingFatRule:
    """The safety gate rejects a fried dish with no fat; the prompt says so."""

    @pytest.mark.asyncio
    async def test_the_recipe_call_asks_for_the_oil(self):
        model = _Model(["Grilled chicken bowl"])

        await generate_week_of_dishes(
            _requests(1), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        recipe_prompt = model.prompts[1]
        assert "stir-fried" in recipe_prompt
        assert "cooking oil or fat" in recipe_prompt

    def test_the_rule_names_what_the_reviewer_looks_for(self):
        """A rule the gate cannot read is a rule that buys nothing."""
        from src.domain.services.weekly_meal_planner.ai_dish import (
            _draft_of,
            snapshot_from_ai_recipe,
        )
        from src.domain.services.weekly_meal_planner.recipe_publication import (
            review_recipe_safety,
        )

        fried = snapshot_from_ai_recipe(
            {
                "name": "Stir-fried greens",
                "ingredients": [
                    {"name": "bok choy", "amount": 200, "unit": "g", "carbs": 4},
                    {"name": "cooking oil", "amount": 10, "unit": "g", "fat": 10},
                ],
                "recipe_steps": [{"instruction": "Stir-fry until just wilted."}],
            },
            dish_id="ai-1",
        )

        assert "a fried dish must list its cooking fat" not in review_recipe_safety(
            _draft_of(fried)
        )


class TestADishThatNamesItself:
    """A slot with no name left to use must still come back with a dish."""

    def test_the_other_key_the_model_reaches_for_is_read(self):
        from src.domain.services.weekly_meal_planner.ai_dish import (
            snapshot_from_ai_recipe,
        )

        snapshot = snapshot_from_ai_recipe(
            {
                "dish_name": "Cơm gà Hội An",
                "ingredients": [
                    {"name": "jasmine rice", "amount": 150, "unit": "g", "carbs": 45},
                    {
                        "name": "chicken breast",
                        "amount": 120,
                        "unit": "g",
                        "protein": 30,
                    },
                ],
                "recipe_steps": [{"instruction": "Luộc gà rồi xé nhỏ."}],
            },
            dish_id="ai-1",
        )

        assert snapshot is not None
        assert snapshot.name == "Cơm gà Hội An"

    @pytest.mark.asyncio
    async def test_a_slot_with_no_name_asks_the_model_to_invent_one(self):
        """Three slots, two names: the third must not ask for a nameless dish."""
        model = _Model(["Grilled chicken bowl", "Lemongrass pork"])

        week = await generate_week_of_dishes(
            _requests(3), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        nameless = [p for p in model.prompts if "Invent one" in p]
        assert nameless, "the third slot asked for a recipe with no name at all"
        assert len(week.dishes) >= 2

    @pytest.mark.asyncio
    async def test_a_name_from_the_week_still_wins_over_the_model_s_own(self):
        model = _Model(["Grilled chicken bowl"])

        week = await generate_week_of_dishes(
            _requests(1), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        assert [d.name for d in week.dishes.values()] == ["Grilled chicken bowl"]


class TestWhichCallSpeaksWhichLanguage:
    """Names are English scaffolding; the recipe call writes what is kept."""

    @pytest.mark.asyncio
    async def test_the_names_call_stays_english_even_for_a_vietnamese_week(self):
        model = _Model(["Gà nướng sả"])

        await generate_week_of_dishes(
            _requests(1),
            ask=model.ask,
            language="vi",
            new_id=lambda: "x",
            **BUILDERS,
        )

        assert "ENGLISH ONLY" in model.prompts[0]
        assert "local_name" not in model.prompts[0]

    @pytest.mark.asyncio
    async def test_only_the_recipe_call_is_asked_for_vietnamese(self):
        model = _Model(["Grilled chicken bowl"])

        await generate_week_of_dishes(
            _requests(1),
            ask=model.ask,
            language="vi",
            new_id=lambda: "x",
            **BUILDERS,
        )

        assert "Vietnamese" in model.prompts[1]
        assert "local_name" in model.prompts[1]

    @pytest.mark.asyncio
    async def test_the_names_call_asks_for_a_different_protein_each_time(self):
        """One call writes every name, so excluding taken names buys nothing."""
        model = _Model(["Grilled chicken bowl", "Lemongrass pork"])

        await generate_week_of_dishes(
            _requests(2), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        assert "different main protein" in model.prompts[0]

    def test_the_shared_builder_is_left_alone_for_everyone_else(self):
        prompt = PromptTemplateManager.build_discovery_prompt(
            meal_type="lunch", target_calories=600, count=3
        )

        assert "ENGLISH ONLY" in prompt


class TestTheNameTheWeekKeeps:
    """The English name is pinned hard, so the Vietnamese one is its own field."""

    @pytest.mark.asyncio
    async def test_a_vietnamese_week_asks_for_a_local_name_field(self):
        model = _Model(["Grilled chicken bowl"])

        await generate_week_of_dishes(
            _requests(1),
            ask=model.ask,
            language="vi",
            new_id=lambda: "x",
            **BUILDERS,
        )

        recipe_prompt = model.prompts[1]
        assert "local_name" in recipe_prompt
        # The dish itself stays pinned: only the name shown changes.
        assert "Match name" in recipe_prompt

    @pytest.mark.asyncio
    async def test_an_english_week_asks_for_no_such_field(self):
        model = _Model(["Grilled chicken bowl"])

        await generate_week_of_dishes(
            _requests(1), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        assert "local_name" not in model.prompts[1]

    def test_the_local_name_is_what_reaches_the_slot(self):
        from src.domain.services.weekly_meal_planner.ai_dish import (
            snapshot_from_ai_recipe,
        )

        snapshot = snapshot_from_ai_recipe(
            {
                "name": "Lemongrass Chicken Rice Bowl",
                "local_name": "Cơm gà nướng sả",
                "ingredients": [
                    {"name": "jasmine rice", "amount": 150, "unit": "g", "carbs": 45},
                    {"name": "chicken", "amount": 120, "unit": "g", "protein": 30},
                ],
                "recipe_steps": [{"instruction": "Nướng gà cho chín vàng."}],
            },
            dish_id="ai-1",
            language="vi",
        )

        assert snapshot.name == "Cơm gà nướng sả"

    def test_a_missing_local_name_falls_back_to_the_english_one(self):
        from src.domain.services.weekly_meal_planner.ai_dish import (
            snapshot_from_ai_recipe,
        )

        snapshot = snapshot_from_ai_recipe(
            {
                "name": "Lemongrass Chicken Rice Bowl",
                "ingredients": [
                    {"name": "jasmine rice", "amount": 150, "unit": "g", "carbs": 45},
                    {"name": "chicken", "amount": 120, "unit": "g", "protein": 30},
                ],
                "recipe_steps": [{"instruction": "Grill the chicken."}],
            },
            dish_id="ai-1",
            language="vi",
        )

        assert snapshot.name == "Lemongrass Chicken Rice Bowl"


class TestTheSameDishNamedTwice:
    def test_word_order_does_not_make_a_second_dish(self):
        from src.domain.services.weekly_meal_planner.ai_dish import dish_key

        assert dish_key("Canh gừng đậu hũ") == dish_key("Canh đậu hũ gừng")

    def test_accents_and_case_still_fold(self):
        from src.domain.services.weekly_meal_planner.ai_dish import dish_key

        assert dish_key("Cá Kho Tộ") == dish_key("ca kho to")

    def test_different_dishes_stay_different(self):
        from src.domain.services.weekly_meal_planner.ai_dish import dish_key

        assert dish_key("Cơm gà xối mỡ") != dish_key("Cơm gà nướng sả")
        assert dish_key("Canh chua cá") != dish_key("Canh chua tôm")

    @pytest.mark.asyncio
    async def test_the_week_turns_the_second_one_away(self):
        model = _Model(
            ["Ginger tofu soup", "Tofu ginger soup", "Lemongrass pork"],
            recipes={
                "Ginger tofu soup": _recipe("Canh gừng đậu hũ"),
                "Tofu ginger soup": _recipe("Canh đậu hũ gừng"),
                "Lemongrass pork": _recipe("Thịt heo nướng sả"),
            },
        )

        week = await generate_week_of_dishes(
            _requests(2), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        names = sorted(dish.name for dish in week.dishes.values())
        assert names == ["Canh gừng đậu hũ", "Thịt heo nướng sả"]
