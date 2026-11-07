"""Asking a model for a week of dishes, with nothing real behind it."""

from types import SimpleNamespace

import pytest

from src.domain.services.prompts.prompt_template_manager import PromptTemplateManager
from src.domain.services.weekly_meal_planner.ai_dish import (
    DishRequest,
    generate_week_of_dishes,
    language_block,
    reference_block,
    review_ai_dish,
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
                # Carries the rest of a real lunch, and lands the fixture
                # at 97% of the 600 kcal slot the tests ask for. A dish that
                # comes to a fifth of its slot is turned away for being off
                # target, which is the point, but it must not take every
                # fixture with it - and the band is tight enough now that a
                # fixture at four fifths of target would trip it too.
                "name": "rice and steamed greens",
                "amount": 220,
                "unit": "g",
                "protein": 3,
                "carbs": 85,
                "fat": 12,
                "fiber": 3,
            }
        )
    return {
        "name": name,
        "prep_time_minutes": 20,
        "ingredients": lines,
        "recipe_steps": steps
        or [
            {"instruction": "Season the chicken and steam the greens."},
            {"instruction": "Grill for 12 minutes until cooked through."},
        ],
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
async def test_each_dish_is_passed_on_the_moment_its_slot_keeps_it():
    """So its photo is made while the next dish is still being written."""
    model = _Model(["Grilled chicken bowl", "Lemongrass pork", "Steamed fish plate"])
    heard = []

    week = await generate_week_of_dishes(
        _requests(3),
        ask=model.ask,
        new_id=lambda: "x",
        on_dish=lambda dish: heard.append((dish, len(model.prompts))),
        **BUILDERS,
    )

    assert [dish for dish, _ in heard] == list(week.dishes.values())
    # The first dish was heard of before the last recipe was even asked for.
    assert heard[0][1] < heard[-1][1] == len(model.prompts)


@pytest.mark.asyncio
async def test_a_listener_that_fails_does_not_cost_the_week():
    model = _Model(["Grilled chicken bowl", "Lemongrass pork", "Steamed fish plate"])

    def broken(dish):
        raise RuntimeError("the photo service is down")

    week = await generate_week_of_dishes(
        _requests(3), ask=model.ask, new_id=lambda: "x", on_dish=broken, **BUILDERS
    )

    assert len(week.dishes) == 3
    assert week.unfilled == ()


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
async def test_the_catalogue_and_the_user_s_own_log_reach_the_names_call():
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
    assert "Prefer a dish" in names_prompt
    assert "Cuisines eaten: Vietnamese" in names_prompt


class TestTheReferenceBlock:
    def test_nothing_to_show_says_nothing(self):
        assert reference_block(()) == ""
        assert reference_block(("", "   ")) == ""

    def test_a_long_catalogue_is_cut_down(self):
        """Forty rows is a knowledge base; the whole table is a token bill."""
        block = reference_block(tuple(f"Dish {index}" for index in range(60)))

        assert "Dish 39" in block
        assert "Dish 40" not in block


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
                "recipe_steps": [
                    {"instruction": "Prepare the ingredients."},
                    {"instruction": "Nấu cơm."},
                ],
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


class TestTheKitchenRules:
    """Every gate the safety reviewer runs, said to the model up front."""

    def test_the_rules_name_what_the_reviewer_reads(self):
        """A rule the gate cannot read buys nothing; drift must break this."""
        from src.domain.services.weekly_meal_planner import recipe_publication as rp
        from src.domain.services.weekly_meal_planner.ai_dish import KITCHEN_RULES

        text = KITCHEN_RULES.casefold()
        for word in ("chín", "luộc", "hấp", "kho", "nướng"):
            assert word in rp._DONENESS_WORDS, f"{word} left the gate"
            assert word in text
        for word in ("tái", "sống", "sashimi"):
            assert word in rp._RAW_PREPARATIONS
            assert word in text
        assert "stir-fried" in text and "stir-fry" in rp._FRYING_WORDS
        assert "oil" in text and "oil" in rp._FAT_WORDS

    @pytest.mark.asyncio
    async def test_the_recipe_call_asks_for_the_oil(self):
        model = _Model(["Grilled chicken bowl"])

        await generate_week_of_dishes(
            _requests(1), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        recipe_prompt = model.prompts[1]
        assert "stir-fried" in recipe_prompt
        assert "cooking oil or fat" in recipe_prompt
        assert "cooked through" in recipe_prompt
        assert "no tái" in recipe_prompt

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
                "recipe_steps": [
                    {"instruction": "Prepare the ingredients."},
                    {"instruction": "Stir-fry until just wilted."},
                ],
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
                "recipe_steps": [
                    {"instruction": "Prepare the ingredients."},
                    {"instruction": "Luộc gà rồi xé nhỏ."},
                ],
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
                "recipe_steps": [
                    {"instruction": "Prepare the ingredients."},
                    {"instruction": "Nướng gà cho chín vàng."},
                ],
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
                "recipe_steps": [
                    {"instruction": "Prepare the ingredients."},
                    {"instruction": "Grill the chicken."},
                ],
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


class TestTwiceTheSameProteinInOneDay:
    """Meal types are written one at a time, so a day must remember itself."""

    def test_the_protein_is_read_off_the_priced_lines(self):
        from src.domain.services.weekly_meal_planner.ai_dish import (
            main_protein,
            snapshot_from_ai_recipe,
        )

        snapshot = snapshot_from_ai_recipe(
            {
                "name": "Cơm gà",
                "ingredients": [
                    {
                        "name": "jasmine rice",
                        "amount": 200,
                        "unit": "g",
                        "protein": 5,
                        "carbs": 56,
                    },
                    {
                        "name": "chicken thigh",
                        "amount": 150,
                        "unit": "g",
                        "protein": 38,
                    },
                ],
                "recipe_steps": [
                    {"instruction": "Prepare the ingredients."},
                    {"instruction": "Nấu cơm."},
                ],
            },
            dish_id="ai-1",
        )

        assert main_protein(snapshot) == "chicken"

    def test_a_cut_does_not_make_a_second_protein(self):
        from src.domain.services.weekly_meal_planner.ai_dish import (
            main_protein,
            snapshot_from_ai_recipe,
        )

        def dish(cut):
            return snapshot_from_ai_recipe(
                {
                    "name": cut,
                    "ingredients": [
                        {"name": cut, "amount": 150, "unit": "g", "protein": 38},
                        {"name": "carrot", "amount": 60, "unit": "g", "carbs": 6},
                    ],
                    "recipe_steps": [
                        {"instruction": "Prepare the ingredients."},
                        {"instruction": "Nấu."},
                    ],
                },
                dish_id="ai-1",
            )

        assert main_protein(dish("chicken thigh")) == main_protein(
            dish("chicken breast")
        )

    def test_a_dish_with_no_protein_names_none(self):
        from src.domain.services.weekly_meal_planner.ai_dish import (
            main_protein,
            snapshot_from_ai_recipe,
        )

        snapshot = snapshot_from_ai_recipe(
            {
                "name": "Rau luộc",
                "ingredients": [
                    {"name": "bok choy", "amount": 200, "unit": "g", "carbs": 4},
                    {"name": "carrot", "amount": 60, "unit": "g", "carbs": 6},
                ],
                "recipe_steps": [
                    {"instruction": "Prepare the ingredients."},
                    {"instruction": "Luộc rau."},
                ],
            },
            dish_id="ai-1",
        )

        assert main_protein(snapshot) == ""

    @pytest.mark.asyncio
    async def test_dinner_is_told_what_lunch_was(self):
        model = _Model(["Lunch bowl", "Dinner plate"])

        await generate_week_of_dishes(
            (
                DishRequest(
                    day_index=0, slot_index=1, meal_type="lunch", target_calories=600
                ),
                DishRequest(
                    day_index=0, slot_index=2, meal_type="dinner", target_calories=600
                ),
            ),
            ask=model.ask,
            new_id=lambda: "x",
            **BUILDERS,
        )

        dinner_prompt = model.prompts[-1]
        assert "Earlier today this person eats" in dinner_prompt
        assert "different main protein" in dinner_prompt

    @pytest.mark.asyncio
    async def test_the_first_meal_of_a_day_is_told_nothing(self):
        model = _Model(["Lunch bowl"])

        await generate_week_of_dishes(
            (
                DishRequest(
                    day_index=0, slot_index=1, meal_type="lunch", target_calories=600
                ),
            ),
            ask=model.ask,
            new_id=lambda: "x",
            **BUILDERS,
        )

        assert "Earlier today" not in model.prompts[1]

    @staticmethod
    def _of(name, protein_line):
        """A distinct dish, so the week's name check is not what fires."""
        return {
            "name": name,
            "ingredients": [
                {**protein_line, "amount": 150, "unit": "g"},
                # Enough to land near a 600 kcal slot, so the off-target
                # check is not what these tests end up measuring: the
                # chicken build comes to 582 kcal and the salmon one to 712,
                # both inside the band a dish is taken on without asking
                # for another name.
                {
                    "name": "rice and carrot",
                    "amount": 220,
                    "unit": "g",
                    "carbs": 85,
                    "fat": 10,
                },
            ],
            "recipe_steps": [
                {"instruction": "Prepare the ingredients."},
                {"instruction": "Nấu chín kỹ rồi dọn ra."},
            ],
        }

    @pytest.mark.asyncio
    async def test_a_name_that_gives_the_protein_away_costs_no_try_at_all(self):
        """Reordering the names catches it before anything is cooked."""
        chicken = {"name": "chicken breast", "protein": 38}
        salmon = {"name": "salmon fillet", "protein": 30, "fat": 18}
        model = _Model(
            ["Chicken lunch", "Chicken dinner", "Fish dinner"],
            recipes={
                "Chicken lunch": self._of("Gà nướng", chicken),
                "Chicken dinner": self._of("Gà kho", chicken),
                "Fish dinner": self._of("Cá hấp", salmon),
            },
        )

        week = await generate_week_of_dishes(
            (
                DishRequest(
                    day_index=0,
                    slot_index=1,
                    meal_type="lunch",
                    target_calories=600,
                ),
                DishRequest(
                    day_index=0,
                    slot_index=2,
                    meal_type="dinner",
                    target_calories=600,
                ),
            ),
            ask=model.ask,
            new_id=lambda: "x",
            **BUILDERS,
        )

        assert sorted(d.name for d in week.dishes.values()) == ["Cá hấp", "Gà nướng"]
        assert week.rejected == ()

    @pytest.mark.asyncio
    async def test_a_name_that_hides_its_protein_is_caught_after_cooking(self):
        """Reordering cannot place "Lunch bowl"; the priced lines can."""
        chicken = {"name": "chicken breast", "protein": 38}
        salmon = {"name": "salmon fillet", "protein": 30, "fat": 18}
        model = _Model(
            ["Lunch bowl", "Dinner bowl", "Dinner plate"],
            recipes={
                "Lunch bowl": self._of("Bữa trưa", chicken),
                "Dinner bowl": self._of("Bữa tối một", chicken),
                "Dinner plate": self._of("Bữa tối hai", salmon),
            },
        )

        week = await generate_week_of_dishes(
            (
                DishRequest(
                    day_index=0,
                    slot_index=1,
                    meal_type="lunch",
                    target_calories=600,
                ),
                DishRequest(
                    day_index=0,
                    slot_index=2,
                    meal_type="dinner",
                    target_calories=600,
                ),
            ),
            ask=model.ask,
            new_id=lambda: "x",
            **BUILDERS,
        )

        assert sorted(d.name for d in week.dishes.values()) == [
            "Bữa trưa",
            "Bữa tối hai",
        ]
        assert any(r.reasons == ("SAME_PROTEIN_TODAY_CHICKEN",) for r in week.rejected)

    @pytest.mark.asyncio
    async def test_the_last_try_takes_what_it_is_given(self):
        """An empty slot is worse than a second helping of chicken."""
        chicken = {"name": "chicken breast", "protein": 38}
        model = _Model(
            ["Chicken lunch", "Chicken dinner", "More chicken"],
            recipes={
                "Chicken lunch": self._of("Gà nướng", chicken),
                "Chicken dinner": self._of("Gà kho", chicken),
                # Not a stir-fry: "xào" with no oil trips the safety gate,
                # which would hide what this test is about.
                "More chicken": self._of("Gà luộc", chicken),
            },
        )

        week = await generate_week_of_dishes(
            (
                DishRequest(
                    day_index=0,
                    slot_index=1,
                    meal_type="lunch",
                    target_calories=600,
                ),
                DishRequest(
                    day_index=0,
                    slot_index=2,
                    meal_type="dinner",
                    target_calories=600,
                ),
            ),
            ask=model.ask,
            new_id=lambda: "x",
            **BUILDERS,
        )

        assert week.unfilled == ()
        assert sorted(d.name for d in week.dishes.values()) == ["Gà luộc", "Gà nướng"]
        assert [r.reasons for r in week.rejected] == [("SAME_PROTEIN_TODAY_CHICKEN",)]

    @pytest.mark.asyncio
    async def test_twice_the_same_taste_in_one_day_asks_for_another_name(self):
        """Staging opened with Chả giò bơ, Bít tết bơ tỏi, Tôm rang bơ tỏi.

        Three proteins, one taste. The protein rule reads that day as
        varied, and nobody who ate it would.
        """
        beef = {"name": "beef sirloin", "protein": 38}
        shrimp = {"name": "shrimp", "protein": 30}
        fish = {"name": "white fish", "protein": 32}
        model = _Model(
            ["Beef lunch", "Shrimp dinner", "Fish dinner"],
            recipes={
                "Beef lunch": self._of("Bít tết bơ tỏi", beef),
                "Shrimp dinner": self._of("Tôm rang bơ tỏi", shrimp),
                "Fish dinner": self._of("Cá hấp gừng", fish),
            },
        )

        week = await generate_week_of_dishes(
            (
                DishRequest(
                    day_index=0, slot_index=1, meal_type="lunch", target_calories=600
                ),
                DishRequest(
                    day_index=0, slot_index=2, meal_type="dinner", target_calories=600
                ),
            ),
            ask=model.ask,
            new_id=lambda: "x",
            **BUILDERS,
        )

        assert sorted(d.name for d in week.dishes.values()) == [
            "Bít tết bơ tỏi",
            "Cá hấp gừng",
        ]
        assert any(r.reasons == ("SAME_FLAVOUR_TODAY_BƠ",) for r in week.rejected), [
            r.reasons for r in week.rejected
        ]

    @pytest.mark.asyncio
    async def test_a_day_of_plain_names_is_left_alone(self):
        """Most names say nothing about seasoning; none of them collide."""
        beef = {"name": "beef sirloin", "protein": 38}
        fish = {"name": "white fish", "protein": 32}
        model = _Model(
            ["Beef lunch", "Fish dinner"],
            recipes={
                "Beef lunch": self._of("Phở bò chín", beef),
                "Fish dinner": self._of("Cháo cá", fish),
            },
        )

        week = await generate_week_of_dishes(
            (
                DishRequest(
                    day_index=0, slot_index=1, meal_type="lunch", target_calories=600
                ),
                DishRequest(
                    day_index=0, slot_index=2, meal_type="dinner", target_calories=600
                ),
            ),
            ask=model.ask,
            new_id=lambda: "x",
            **BUILDERS,
        )

        assert len(week.dishes) == 2
        assert week.rejected == ()

    @pytest.mark.asyncio
    async def test_another_day_may_cook_the_same_taste(self):
        """A taste is a rule about one day, like its protein."""
        beef = {"name": "beef sirloin", "protein": 38}
        model = _Model(
            ["Lunch one", "Lunch two"],
            recipes={
                "Lunch one": self._of("Bò kho sả", beef),
                "Lunch two": self._of(
                    "Gà kho sả", {"name": "chicken breast", "protein": 36}
                ),
            },
        )

        week = await generate_week_of_dishes(
            _requests(2), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        assert len(week.dishes) == 2
        assert week.rejected == ()

    @pytest.mark.asyncio
    async def test_four_oat_mornings_in_a_week_ask_for_another_name(self):
        """The locale note asked and was ignored, so it is checked instead.

        A full-week run answered cháo yến mạch on four of seven mornings -
        khoai lang trứng, bí đỏ trứng, gà bí đỏ, trứng gà bí đỏ. Four dish
        keys, one breakfast.
        """
        egg = {"name": "egg", "protein": 30}
        model = _Model(
            ["Oats one", "Oats two", "Bread"],
            recipes={
                "Oats one": self._of("Cháo yến mạch khoai lang trứng", egg),
                "Oats two": self._of("Cháo yến mạch bí đỏ trứng", egg),
                "Bread": self._of("Bánh mì trứng ốp la", egg),
            },
        )

        week = await generate_week_of_dishes(
            tuple(
                DishRequest(
                    day_index=day,
                    slot_index=0,
                    meal_type="breakfast",
                    target_calories=600,
                    vary_base=True,
                )
                for day in range(2)
            ),
            ask=model.ask,
            new_id=lambda: "x",
            **BUILDERS,
        )

        assert sorted(d.name for d in week.dishes.values()) == [
            "Bánh mì trứng ốp la",
            "Cháo yến mạch khoai lang trứng",
        ]
        assert any(
            r.reasons == ("SAME_BASE_THIS_WEEK_YẾN MẠCH",) for r in week.rejected
        ), [r.reasons for r in week.rejected]

    @pytest.mark.asyncio
    async def test_lunch_may_be_rice_every_day(self):
        """Nothing asks lunch to vary its base, and rice twice is the point."""
        beef = {"name": "beef sirloin", "protein": 38}
        fish = {"name": "white fish", "protein": 32}
        model = _Model(
            ["Lunch one", "Lunch two"],
            recipes={
                "Lunch one": self._of("Bún bò Huế", beef),
                "Lunch two": self._of("Bún cá", fish),
            },
        )

        week = await generate_week_of_dishes(
            _requests(2), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        assert len(week.dishes) == 2
        assert week.rejected == ()

    @pytest.mark.asyncio
    async def test_a_slot_whose_names_are_all_taken_still_gets_a_dish(self):
        """Day 7 lost its lunch this way in a full-week run.

        Both names came back as dishes the week already held - a hard no,
        which keeps nothing - and the slot's two tries were spent. The spare
        try now goes to a hard no as well, and with no name left it asks the
        model to name its own.
        """
        beef = {"name": "beef sirloin", "protein": 38}
        model = _Model(
            ["Lunch one", "Lunch two", "Lunch three"],
            recipes={
                "Lunch one": self._of("Bò kho", beef),
                # Both of day two's names cook into day one's dish.
                "Lunch two": self._of("Bò kho", beef),
                "Lunch three": self._of("Bò kho", beef),
                # What it writes when asked to name its own.
                "": self._of("Cá hấp", {"name": "white fish", "protein": 32}),
            },
        )

        week = await generate_week_of_dishes(
            _requests(2), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        assert week.unfilled == ()
        assert sorted(d.name for d in week.dishes.values()) == ["Bò kho", "Cá hấp"]

    @pytest.mark.asyncio
    async def test_another_day_may_eat_the_same_protein(self):
        """Variety is a rule about one day, not about the week."""
        model = _Model(["Chicken one", "Chicken two"])

        week = await generate_week_of_dishes(
            (
                DishRequest(
                    day_index=0, slot_index=1, meal_type="lunch", target_calories=600
                ),
                DishRequest(
                    day_index=1, slot_index=1, meal_type="lunch", target_calories=600
                ),
            ),
            ask=model.ask,
            new_id=lambda: "x",
            **BUILDERS,
        )

        assert len(week.dishes) == 2


class TestASpareNameForTheLastSlot:
    @pytest.mark.asyncio
    async def test_more_names_are_asked_for_than_slots_need(self):
        model = _Model(["A", "B", "C", "D", "E"])

        await generate_week_of_dishes(
            _requests(3), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        assert "Generate 5 different" in model.prompts[0]


class TestTheCatalogueAsKnowledge:
    """The catalogue is what this kitchen knows, not a style hint.

    The block used to end "Write your own dish. Do not reuse these names."
    Measured against one catalogue sample, three wordings put 0, 19 and 20
    of 21 real dishes on the table; the one that allowed nothing but the
    list pasted a blog headline into a slot. This is the middle one.
    """

    CATALOG = ("Thịt kho tàu", "Canh chua cá lóc", "Phở bò")

    def test_the_catalogue_is_offered_rather_than_forbidden(self):
        block = reference_block(self.CATALOG)

        assert "Prefer a dish" in block
        assert "Do not reuse these names" not in block

    def test_a_headline_is_to_be_read_for_its_dish(self):
        """A crawled table is only usable if the dish can be pulled out."""
        block = reference_block(("Gỏi cuốn tôm thịt chuẩn vị miền Tây ai cũng mê",))

        assert "take the dish out of it" in block
        assert "not the headline" in block

    def test_the_cross_product_that_made_pho_ca_hoi_is_named(self):
        assert "combining one dish's base with another's protein" in (
            reference_block(self.CATALOG)
        )

    def test_a_dish_outside_the_list_must_still_be_a_real_one(self):
        assert "a home cook here would recognise" in reference_block(self.CATALOG)

    @pytest.mark.asyncio
    async def test_each_meal_type_sees_its_own_catalogue_rows(self):
        model = _Model(["Cháo gà", "Thịt kho tàu"])

        await generate_week_of_dishes(
            (
                DishRequest(
                    day_index=0,
                    slot_index=0,
                    meal_type="breakfast",
                    target_calories=450,
                ),
                DishRequest(
                    day_index=0,
                    slot_index=1,
                    meal_type="lunch",
                    target_calories=675,
                ),
            ),
            ask=model.ask,
            reference_dishes={
                "breakfast": ("Cháo gà", "Xôi xéo"),
                "lunch": ("Thịt kho tàu", "Cá kho tộ"),
            },
            new_id=lambda: "x",
            **BUILDERS,
        )

        names_calls = [p for p in model.prompts if "meals, each" in p]
        assert len(names_calls) == 2
        breakfast, lunch = names_calls
        assert "Xôi xéo" in breakfast and "Cá kho tộ" not in breakfast
        assert "Cá kho tộ" in lunch and "Xôi xéo" not in lunch

    @pytest.mark.asyncio
    async def test_a_flat_catalogue_is_shown_to_every_meal(self):
        """The older shape still works; every meal type sees all of it."""
        model = _Model(["Cháo gà"])

        await generate_week_of_dishes(
            _requests(1),
            ask=model.ask,
            reference_dishes=("Thịt kho tàu", "Canh chua cá lóc"),
            new_id=lambda: "x",
            **BUILDERS,
        )

        assert "Thịt kho tàu" in model.prompts[0]


class TestOrderingWhatTheModelNamed:
    """One answer names a whole meal type, knowing nothing of the days."""

    def test_the_second_meal_steps_past_the_day_s_protein(self):
        from src.domain.services.weekly_meal_planner.ai_dish import _arrange_by_day

        names = ["Gà kho gừng", "Cá kho tộ", "Bò lúc lắc"]

        assert _arrange_by_day(names, ("chicken", "beef", "")) == [
            "Cá kho tộ",
            "Gà kho gừng",
            "Bò lúc lắc",
        ]

    def test_nothing_to_avoid_leaves_the_order_alone(self):
        from src.domain.services.weekly_meal_planner.ai_dish import _arrange_by_day

        names = ["Gà kho gừng", "Cá kho tộ"]
        assert _arrange_by_day(names, ()) == names

    def test_no_way_round_it_still_returns_every_name(self):
        from src.domain.services.weekly_meal_planner.ai_dish import _arrange_by_day

        names = ["Gà kho gừng", "Gà rang muối"]
        assert sorted(_arrange_by_day(names, ("chicken", "chicken"))) == sorted(names)


class TestReadingTheProteinOffTheName:
    def test_the_region_s_own_names_are_placed(self):
        from src.domain.services.weekly_meal_planner.ai_dish import protein_of_name

        assert protein_of_name("Gà kho gừng") == "chicken"
        assert protein_of_name("Bò lúc lắc") == "beef"
        assert protein_of_name("Cá kho tộ") == "fish"
        assert protein_of_name("Tôm rang me") == "shrimp"
        assert protein_of_name("Đậu hũ sốt cà chua") == "tofu"
        assert protein_of_name("Sườn ram mặn") == "pork"

    def test_a_name_that_says_nothing_places_nothing(self):
        from src.domain.services.weekly_meal_planner.ai_dish import protein_of_name

        assert protein_of_name("Xôi đậu xanh") == ""
        assert protein_of_name("") == ""

    def test_a_tomato_is_not_a_fish(self):
        """dish_key folds "cà chua" to "ca"; this must not read it as "cá"."""
        from src.domain.services.weekly_meal_planner.ai_dish import protein_of_name

        assert protein_of_name("Đậu hũ sốt cà chua") == "tofu"
        assert protein_of_name("Cà tím nướng mỡ hành") == ""
        assert protein_of_name("Cá basa kho tiêu") == "fish"


class TestADishThatIsFoodButNotThisMeal:
    """80-1600 kcal says "this is food"; it says nothing about the slot."""

    @staticmethod
    def _at(kcal):
        """A dish priced to roughly kcal, through the macro sum."""
        protein = kcal / 4.0 / 2
        carbs = kcal / 4.0 / 2
        from src.domain.services.weekly_meal_planner.ai_dish import (
            snapshot_from_ai_recipe,
        )

        return snapshot_from_ai_recipe(
            {
                "name": "Cơm gà",
                "ingredients": [
                    {"name": "chicken", "amount": 150, "unit": "g", "protein": protein},
                    {"name": "rice", "amount": 200, "unit": "g", "carbs": carbs},
                ],
                "recipe_steps": [
                    {"instruction": "Prepare the ingredients."},
                    {"instruction": "Nấu chín kỹ."},
                ],
            },
            dish_id="ai-1",
        )

    def test_a_dish_twice_its_slot_is_turned_away(self):
        """1,580 kcal into a slot asking 675 made a 3,320 kcal day."""
        dish = self._at(1400)

        reasons = review_ai_dish(dish, target_calories=675)

        assert any(r.startswith("OFF_TARGET_CALORIES") for r in reasons)
        # Still food by the absolute bounds, which is why this was needed.
        assert "IMPLAUSIBLE_CALORIES" not in review_ai_dish(dish)

    def test_a_dish_a_third_of_its_slot_is_turned_away(self):
        assert any(
            r.startswith("OFF_TARGET_CALORIES")
            for r in review_ai_dish(self._at(200), target_calories=675)
        )

    def test_a_dish_that_landed_on_its_slot_is_taken_as_it_is(self):
        """Inside the band nothing is said, so no second name is asked for."""
        for share in (0.9, 1.0, 1.1, 1.2):
            dish = self._at(675 * share)
            assert not any(
                r.startswith("OFF_TARGET")
                for r in review_ai_dish(dish, target_calories=675)
            ), f"{share:.0%} of target should be taken as it is"

    def test_a_dish_a_sixth_short_asks_for_another_name(self):
        """Staging's week: every slot inside 0.6-1.5, days a fifth short.

        Three dishes at 0.8 is a day at 0.8. Being under is as wrong as
        being over, and saying so costs one name, never the slot.
        """
        for share in (0.75, 0.8, 1.35):
            assert any(
                r.startswith("OFF_TARGET")
                for r in review_ai_dish(self._at(675 * share), target_calories=675)
            ), f"{share:.0%} of target should ask for another name"

    @pytest.mark.asyncio
    async def test_a_slot_whose_dishes_are_all_short_still_gets_the_closest(self):
        """Off target is never worth an empty slot - but take the best one."""
        model = _Model(
            ["Small lunch", "Smaller lunch"],
            recipes={
                "Small lunch": _recipe("Canh suông", protein=25),
                "Smaller lunch": _recipe("Canh rau", protein=4),
            },
        )

        week = await generate_week_of_dishes(
            _requests(1, kcal=900), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        assert week.unfilled == ()
        # 587 kcal against 900 beats 503 against 900, and the dish the slot
        # keeps is not also reported as one the week turned away.
        assert [d.name for d in week.dishes.values()] == ["Canh suông"]
        assert [r.name for r in week.rejected] == ["Canh rau"]

    def test_no_target_means_no_such_check(self):
        assert not any(
            r.startswith("OFF_TARGET") for r in review_ai_dish(self._at(1400))
        )

    @pytest.mark.asyncio
    async def test_the_slot_s_own_target_is_what_is_checked(self):
        model = _Model(["Huge lunch", "Right lunch"])

        week = await generate_week_of_dishes(
            _requests(1, kcal=600), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        # The shared fixture lands near 600, so nothing is off target.
        assert len(week.dishes) == 1
        assert not any(
            r.startswith("OFF_TARGET") for d in week.rejected for r in d.reasons
        )


class TestWhenOurOwnPricingIsDown:
    """Every tier failing returns zeros, not an exception."""

    @staticmethod
    def _dish():
        from src.domain.services.weekly_meal_planner.ai_dish import (
            snapshot_from_ai_recipe,
        )

        return snapshot_from_ai_recipe(
            {
                "name": "Cơm gà",
                "ingredients": [
                    {"name": "chicken", "amount": 150, "unit": "g"},
                    {"name": "rice", "amount": 200, "unit": "g"},
                ],
                "recipe_steps": [
                    {"instruction": "Prepare the ingredients."},
                    {"instruction": "Nấu chín kỹ."},
                ],
            },
            dish_id="ai-1",
        )

    def test_a_silent_zero_is_a_pricing_failure_not_a_bad_dish(self):
        from src.domain.services.weekly_meal_planner.ai_dish import (
            PricingUnavailable,
            priced_snapshot,
        )

        nothing = SimpleNamespace(
            protein=0.0, carbs=0.0, fat=0.0, fiber=0.0, sugar=0.0, ingredients=[]
        )

        with pytest.raises(PricingUnavailable):
            priced_snapshot(self._dish(), nothing)

    def test_a_priced_dish_is_kept(self):
        from src.domain.services.weekly_meal_planner.ai_dish import priced_snapshot

        priced = SimpleNamespace(
            protein=40.0, carbs=60.0, fat=15.0, fiber=3.0, sugar=1.0, ingredients=[]
        )

        assert priced_snapshot(self._dish(), priced).protein == 40.0

    @pytest.mark.asyncio
    async def test_the_week_stops_rather_than_paying_for_twenty_more(self):
        """Every slot would fail the same way, each costing a recipe call."""
        from src.domain.services.weekly_meal_planner.ai_dish import PricingUnavailable

        model = _Model(["A", "B", "C", "D", "E"])

        async def nothing(lines):
            return SimpleNamespace(
                protein=0.0, carbs=0.0, fat=0.0, fiber=0.0, sugar=0.0, ingredients=[]
            )

        with pytest.raises(PricingUnavailable):
            await generate_week_of_dishes(
                _requests(3),
                ask=model.ask,
                price=nothing,
                new_id=lambda: "x",
                **BUILDERS,
            )

        # One names call and one recipe call, not three recipes.
        assert len([p for p in model.prompts if "Generate complete" in p]) == 1


class TestSoftAndHardNo:
    """A hard no is never served; a soft no is, rather than nothing."""

    @staticmethod
    def _sized(name, kcal):
        """A dish of roughly kcal, spread over enough grams to be food.

        The macros have to fit in the weight: squeezed into 350 g they
        break the per-100g mass bound and the dish is hard-rejected, which
        is a different test.
        """
        half = kcal / 8.0
        grams = max(350.0, (half * 2) / 0.45)
        return {
            "name": name,
            "ingredients": [
                {"name": "chicken", "amount": grams / 2, "unit": "g", "protein": half},
                {"name": "rice", "amount": grams / 2, "unit": "g", "carbs": half},
            ],
            "recipe_steps": [
                {"instruction": "Prepare the ingredients."},
                {"instruction": "Nấu chín kỹ rồi dọn ra."},
            ],
        }

    @pytest.mark.asyncio
    async def test_a_better_sized_dish_is_preferred_when_one_is_left(self):
        model = _Model(
            ["Huge", "Right"],
            recipes={
                "Huge": self._sized("Cơm quá to", 1500),
                "Right": self._sized("Cơm vừa", 620),
            },
        )

        week = await generate_week_of_dishes(
            _requests(1, kcal=600), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        assert [d.name for d in week.dishes.values()] == ["Cơm vừa"]
        assert any(r.startswith("OFF_TARGET") for d in week.rejected for r in d.reasons)

    @pytest.mark.asyncio
    async def test_an_off_target_dish_beats_an_empty_slot(self):
        """Every name is wrong-sized; the slot still gets fed."""
        model = _Model(
            ["One", "Two", "Three"],
            recipes={
                name: self._sized(f"Cơm {name}", 1500)
                for name in ("One", "Two", "Three")
            },
        )

        week = await generate_week_of_dishes(
            _requests(1, kcal=600), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        assert week.unfilled == ()
        assert len(week.dishes) == 1

    @pytest.mark.asyncio
    async def test_an_unsafe_dish_is_never_served_however_few_names_remain(self):
        """Safety is not traded for a filled slot."""
        model = _Model(
            ["Only"],
            recipes={
                "Only": {
                    "name": "Bò tái",
                    "ingredients": [
                        {"name": "beef", "amount": 150, "unit": "g", "protein": 75},
                        {"name": "rice", "amount": 200, "unit": "g", "carbs": 75},
                    ],
                    "recipe_steps": [
                        {"instruction": "Prepare the ingredients."},
                        {"instruction": "Thái bò tái rồi dọn ra."},
                    ],
                }
            },
        )

        week = await generate_week_of_dishes(
            _requests(1, kcal=600), ask=model.ask, new_id=lambda: "x", **BUILDERS
        )

        assert week.dishes == {}
        assert week.unfilled == ((0, 1),)
        assert any("raw or rare" in r for d in week.rejected for r in d.reasons)


class TestTheCatalogueHoldsThingsThatAreNotMeals:
    """Read off staging's own meal_catalog, lunch-eligible, in the order
    candidates() returns: Bingsu Oreo, Bloody Mary cocktail, Bim bim mì
    gói, "Bột ngọt ăn dặm", "Nước mã đề giúp", a weight-loss drink and
    several desserts. Roughly a fifth of the forty rows shown.
    """

    def test_the_model_is_told_to_skip_them(self):
        block = reference_block(("Bingsu Oreo", "Bột ngọt ăn dặm"))

        assert "not a meal somebody sits down to eat" in block
        for kind in ("drink", "dessert", "snack", "baby food", "seasoning"):
            assert kind in block

    def test_it_is_said_as_a_fact_about_the_catalogue(self):
        """Not a warning about the model: the rows really are in there."""
        assert "The catalogue holds those too" in reference_block(("Bingsu",))


# ---------------------------------------------------------------------------
# One call writes a meal type's recipes
# ---------------------------------------------------------------------------


def _section_names(prompt):
    return [_requested_name(part) for part in prompt.split("=== DISH ")[1:]]


class _BatchModel(_Model):
    """A model that also writes a meal type's recipes in one answer."""

    def __init__(self, names, recipes=None, batch=None, batch_fails=False):
        super().__init__(names, recipes)
        self._batch = batch or {}
        self._batch_fails = batch_fails
        self.batch_prompts = []

    async def ask_many(self, prompt, system):
        self.batch_prompts.append(prompt)
        if self._batch_fails:
            raise RuntimeError("the answer was cut off")
        entries = []
        for number, name in enumerate(_section_names(prompt), start=1):
            recipe = self._batch.get(name, self._recipes.get(name, _recipe(name)))
            if recipe is None:
                continue
            entries.append({"dish": number, **recipe})
        return {"recipes": entries}

    @property
    def recipe_prompts(self):
        return [p for p in self.prompts if _requested_name(p)]


THREE = ["Grilled chicken bowl", "Lemongrass pork", "Steamed fish plate"]


@pytest.mark.asyncio
async def test_a_meal_type_s_recipes_come_back_in_one_call():
    model = _BatchModel(THREE)

    week = await generate_week_of_dishes(
        _requests(3),
        ask=model.ask,
        ask_many=model.ask_many,
        new_id=lambda: "x",
        **BUILDERS,
    )

    assert len(week.dishes) == 3
    assert week.unfilled == ()
    assert len(model.batch_prompts) == 1
    assert _section_names(model.batch_prompts[0]) == THREE
    # Not one slot had to ask for its own recipe.
    assert model.recipe_prompts == []


@pytest.mark.asyncio
async def test_a_batch_recipe_is_checked_like_any_other_and_only_its_slot_asks_again():
    unsafe = _recipe(
        "Grilled chicken bowl", steps=[{"instruction": "Slice the raw chicken."}]
    )
    model = _BatchModel(
        [*THREE, "Beef noodle soup"], batch={"Grilled chicken bowl": unsafe}
    )

    week = await generate_week_of_dishes(
        _requests(3),
        ask=model.ask,
        ask_many=model.ask_many,
        new_id=lambda: "x",
        **BUILDERS,
    )

    assert len(week.dishes) == 3
    assert week.rejected[0].name == "Grilled chicken bowl"
    # The slot took the spare name rather than the next slot's, so the
    # rest of the week kept the recipes the batch wrote for it.
    assert [week.dishes[(day, 1)].name for day in range(3)] == [
        "Beef noodle soup",
        "Lemongrass pork",
        "Steamed fish plate",
    ]
    assert [_requested_name(p) for p in model.recipe_prompts] == ["Beef noodle soup"]


@pytest.mark.asyncio
async def test_a_recipe_missing_from_the_batch_is_asked_for_on_its_own():
    model = _BatchModel(THREE, batch={"Lemongrass pork": None})

    week = await generate_week_of_dishes(
        _requests(3),
        ask=model.ask,
        ask_many=model.ask_many,
        new_id=lambda: "x",
        **BUILDERS,
    )

    assert len(week.dishes) == 3
    assert [_requested_name(p) for p in model.recipe_prompts] == ["Lemongrass pork"]


@pytest.mark.asyncio
async def test_a_batch_call_that_fails_falls_back_to_one_call_per_slot():
    model = _BatchModel(THREE, batch_fails=True)

    week = await generate_week_of_dishes(
        _requests(3),
        ask=model.ask,
        ask_many=model.ask_many,
        new_id=lambda: "x",
        **BUILDERS,
    )

    assert len(week.dishes) == 3
    assert len(model.recipe_prompts) == 3


@pytest.mark.asyncio
async def test_batch_entries_land_on_their_own_section_whatever_their_order():
    model = _BatchModel(THREE)

    async def reversed_answer(prompt, system):
        answer = await model.ask_many(prompt, system)
        return {"recipes": list(reversed(answer["recipes"]))}

    week = await generate_week_of_dishes(
        _requests(3),
        ask=model.ask,
        ask_many=reversed_answer,
        new_id=lambda: "x",
        **BUILDERS,
    )

    assert [week.dishes[(day, 1)].name for day in range(3)] == THREE
    assert model.recipe_prompts == []


@pytest.mark.asyncio
async def test_a_later_meal_type_s_batch_knows_what_each_day_already_eats():
    model = _BatchModel(["Beef pho", "Grilled chicken bowl"])
    requests = (
        DishRequest(
            day_index=0, slot_index=0, meal_type="breakfast", target_calories=600
        ),
        DishRequest(day_index=0, slot_index=1, meal_type="lunch", target_calories=600),
    )

    await generate_week_of_dishes(
        requests,
        ask=model.ask,
        ask_many=model.ask_many,
        new_id=lambda: "x",
        **BUILDERS,
    )

    assert len(model.batch_prompts) == 2
    breakfast = model.batch_prompts[0].split("=== DISH ")[1]
    lunch = model.batch_prompts[1].split("=== DISH ")[1]
    assert "Earlier today this person eats" not in breakfast
    assert "Earlier today this person eats: Beef pho" in lunch


def test_a_week_counts_why_its_dishes_were_turned_away():
    from src.domain.services.weekly_meal_planner.ai_dish import (
        RejectedDish,
        WeekOfDishes,
    )

    week = WeekOfDishes(
        dishes={},
        rejected=(
            RejectedDish("a", ("OFF_TARGET_CALORIES_134PCT", "TOO_FEW_STEPS")),
            RejectedDish("b", ("OFF_TARGET_CALORIES_70PCT",)),
            RejectedDish("c", ("SAME_PROTEIN_TODAY_CHICKEN",)),
            RejectedDish("d", ("recipe serves food raw or rare",)),
        ),
    )

    assert week.rejection_counts() == {
        "OFF_TARGET_CALORIES": 2,
        "SAME_PROTEIN_TODAY": 1,
        "TOO_FEW_STEPS": 1,
        "recipe serves food raw or rare": 1,
    }
