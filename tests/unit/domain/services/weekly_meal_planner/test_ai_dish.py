"""A dish the planner invented, checked before it reaches a slot."""

from src.domain.model.weekly_meal_planner.saved_recipe import (
    SavedRecipeIngredient,
    SavedRecipeSnapshot,
    SavedRecipeStep,
)
from src.domain.services.weekly_meal_planner.ai_dish import (
    SOURCE_PLANNER_AI,
    dish_key,
    review_ai_dish,
    snapshot_from_ai_recipe,
)


def _payload(**overrides):
    payload = {
        "name": "Lemongrass Tofu Rice Bowl",
        "prep_time_minutes": 15,
        "emoji": "🍚",
        "cuisine_type": "Vietnamese",
        "ingredients": [
            {
                "name": "firm tofu",
                "amount": 120,
                "unit": "g",
                "protein": 19,
                "carbs": 4,
                "fat": 9,
            },
            {
                "name": "cooked jasmine rice",
                "amount": 150,
                "unit": "g",
                "protein": 4,
                "carbs": 45,
                "fat": 0.4,
                "fiber": 1,
            },
            {
                "name": "bok choy",
                "amount": 80,
                "unit": "g",
                "protein": 1.2,
                "carbs": 2,
                "fat": 0.2,
                "fiber": 1,
            },
        ],
        "recipe_steps": [
            {
                "instruction": "Grill the tofu for 8 minutes until cooked through.",
                "duration_minutes": 8,
            },
            {"instruction": "Steam the bok choy and serve it over the rice."},
        ],
    }
    payload.update(overrides)
    return payload


class TestReadingWhatTheModelWrote:
    def test_a_recipe_becomes_a_slot_snapshot(self):
        snapshot = snapshot_from_ai_recipe(_payload(), dish_id="ai-1", language="vi")

        assert snapshot is not None
        assert snapshot.name == "Lemongrass Tofu Rice Bowl"
        assert snapshot.suggestion_id == "ai-1"
        assert snapshot.source == SOURCE_PLANNER_AI
        assert snapshot.servings == 1
        assert len(snapshot.ingredients) == 3
        assert len(snapshot.steps) == 2
        assert snapshot.cook_time_minutes == 15

    def test_macros_are_summed_from_the_lines(self):
        snapshot = snapshot_from_ai_recipe(_payload(), dish_id="ai-1")

        assert snapshot.protein == 24.2
        assert snapshot.carbs == 51.0
        assert snapshot.fiber == 2.0

    def test_calories_the_model_claims_are_ignored(self):
        """Calories come from the macros, as they do for every logged meal."""
        snapshot = snapshot_from_ai_recipe(
            _payload(calories=5000, total_calories=5000), dish_id="ai-1"
        )

        assert snapshot.calories == 383

    def test_a_step_written_as_plain_text_is_kept(self):
        snapshot = snapshot_from_ai_recipe(
            _payload(recipe_steps=["Boil the noodles for 4 minutes until tender."]),
            dish_id="ai-1",
        )

        assert snapshot.steps[0].instruction.startswith("Boil the noodles")
        assert snapshot.steps[0].duration_minutes is None

    def test_an_unreadable_answer_is_not_half_stored(self):
        assert snapshot_from_ai_recipe({}, dish_id="ai-1") is None
        assert snapshot_from_ai_recipe({"name": "Soup"}, dish_id="ai-1") is None
        assert (
            snapshot_from_ai_recipe({"name": "", "ingredients": []}, dish_id="ai-1")
            is None
        )


class TestWhatMayTakeASlot:
    def test_an_ordinary_dish_passes(self):
        snapshot = snapshot_from_ai_recipe(_payload(), dish_id="ai-1")

        assert review_ai_dish(snapshot) == ()

    def test_a_dish_the_week_already_serves_is_refused(self):
        """The complaint that sent the planner here: one dish, seven days."""
        snapshot = snapshot_from_ai_recipe(_payload(), dish_id="ai-1")
        taken = frozenset({dish_key("lemongrass tofu rice bowl")})

        assert "ALREADY_IN_THE_WEEK" in review_ai_dish(snapshot, taken=taken)

    def test_the_same_dish_in_different_dress_is_still_the_same_dish(self):
        assert dish_key("Cá Kho Tộ") == dish_key("ca kho to")
        assert dish_key("  Phở   Bò ") == dish_key("pho bo")

    def test_a_dish_with_one_ingredient_is_refused(self):
        snapshot = snapshot_from_ai_recipe(
            _payload(
                ingredients=[
                    {
                        "name": "rice",
                        "amount": 500,
                        "unit": "g",
                        "protein": 10,
                        "carbs": 110,
                        "fat": 1,
                    }
                ]
            ),
            dish_id="ai-1",
        )

        assert "TOO_FEW_INGREDIENTS" in review_ai_dish(snapshot)

    def test_a_dish_with_no_steps_is_refused(self):
        snapshot = snapshot_from_ai_recipe(_payload(recipe_steps=[]), dish_id="ai-1")

        assert "NO_STEPS" in review_ai_dish(snapshot)

    def test_a_plate_nobody_could_eat_is_refused(self):
        snapshot = snapshot_from_ai_recipe(
            _payload(
                ingredients=[
                    {"name": "butter", "amount": 400, "unit": "g", "fat": 330},
                    {"name": "sugar", "amount": 300, "unit": "g", "carbs": 300},
                ]
            ),
            dish_id="ai-1",
        )

        assert "IMPLAUSIBLE_CALORIES" in review_ai_dish(snapshot)

    def test_macros_that_could_not_be_food_are_refused(self):
        """A hundred grams of dish cannot hold a hundred and forty of macros."""
        snapshot = SavedRecipeSnapshot(
            suggestion_id="ai-1",
            name="Mystery plate",
            protein=90.0,
            carbs=40.0,
            fat=10.0,
            ingredients=(
                SavedRecipeIngredient(name="mystery powder", grams=50.0),
                SavedRecipeIngredient(name="water", grams=50.0),
            ),
            steps=(SavedRecipeStep(instruction="Stir it together."),),
        )

        reasons = review_ai_dish(snapshot)

        assert any(reason.startswith("IMPLAUSIBLE_MACROS") for reason in reasons)

    def test_an_unsafe_recipe_is_refused_by_the_same_gate_the_catalog_uses(self):
        snapshot = snapshot_from_ai_recipe(
            _payload(
                name="Chicken salad",
                recipe_steps=[
                    {"instruction": "Slice the raw chicken and toss it through."}
                ],
            ),
            dish_id="ai-1",
        )

        assert review_ai_dish(snapshot) != ()
