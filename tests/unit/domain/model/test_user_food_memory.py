"""What the planner remembers about how one person eats."""

from src.domain.model.weekly_meal_planner.meal_locale import VIETNAM
from src.domain.model.weekly_meal_planner.user_food_memory import (
    CONFIDENT_MEAL_COUNT,
    EMPTY_MEMORY,
    LoggedMealShape,
    UserFoodMemory,
    build_user_food_memory,
)


def _meal(name, *items, cuisine="vietnamese"):
    return LoggedMealShape(dish_name=name, cuisine=cuisine, item_names=tuple(items))


def test_a_user_with_no_history_remembers_nothing():
    memory = build_user_food_memory()

    assert memory is EMPTY_MEMORY
    assert memory.meals_observed == 0
    assert memory.prompt_text() == ""
    assert memory.taste_score([1, 2, 3]) == 0.0


def test_eating_rice_with_most_meals_is_remembered_as_a_habit():
    meals = [
        _meal("Thịt kho", "Thịt heo", "Cơm trắng"),
        _meal("Cá kho", "Cá lóc", "Cơm trắng"),
        _meal("Phở gà", "Bánh phở", "Thịt gà"),
    ]

    memory = build_user_food_memory(meals=meals, is_staple_item=VIETNAM.is_staple_item)

    assert memory.meals_observed == 3
    assert memory.staple_share == 2 / 3
    assert memory.items_per_meal == 2.0


def test_the_staple_question_is_regional_so_no_locale_means_no_claim():
    """Without a locale to ask, the planner must not invent the habit."""
    meals = [_meal("Thịt kho", "Thịt heo", "Cơm trắng")]

    memory = build_user_food_memory(meals=meals)

    assert memory.staple_share == 0.0


def test_a_dish_made_of_food_the_user_eats_scores_higher():
    memory = UserFoodMemory(liked_ingredients={1: 0.9, 2: 0.8, 3: 0.05})

    assert memory.taste_score([1, 2]) > memory.taste_score([1, 3])
    assert memory.taste_score([3]) < 0.1
    assert memory.taste_score([]) == 0.0


def test_a_taste_score_never_leaves_its_range():
    memory = UserFoodMemory(liked_ingredients={1: 5.0, 2: 5.0})

    assert memory.taste_score([1, 2]) == 1.0
    assert memory.taste_score([99]) == 0.0


def test_only_food_eaten_often_counts_as_liked():
    memory = UserFoodMemory(liked_ingredients={1: 0.9, 2: 0.02})

    assert memory.liked_ingredient_ids == frozenset({1})


def test_a_short_log_is_not_confident_enough_to_plan_around():
    thin = build_user_food_memory(meals=[_meal("Thịt kho", "Thịt heo")])
    thick = build_user_food_memory(
        meals=[_meal(f"Món {i}", "Thịt heo") for i in range(CONFIDENT_MEAL_COUNT)]
    )

    assert thin.is_confident is False
    assert thick.is_confident is True
    # The shape lines stay out of the prompt until they mean something.
    assert "items in a meal" not in thin.prompt_text()
    assert "items in a meal" in thick.prompt_text()


def test_the_prompt_text_reads_as_facts_about_one_person():
    meals = [
        _meal("Thịt kho", "Thịt heo", "Cơm trắng"),
        _meal("Phở gà", "Bánh phở", cuisine="vietnamese"),
        _meal("Pasta", "Mì Ý", cuisine="italian"),
    ]

    text = build_user_food_memory(
        meals=meals, is_staple_item=VIETNAM.is_staple_item
    ).prompt_text()

    assert "Meals logged: 3" in text
    # Most eaten cuisine first.
    assert "Cuisines eaten: vietnamese, italian" in text
    assert "Thịt kho" in text


def test_the_same_dish_eaten_twice_is_listed_once():
    meals = [_meal("Thịt kho", "Thịt heo"), _meal("Thịt kho", "Thịt heo")]

    memory = build_user_food_memory(meals=meals)

    assert memory.recent_dishes == ("Thịt kho",)
    assert memory.meals_observed == 2


def test_one_skip_is_a_busy_evening_not_a_verdict():
    """Skipping once usually means the user ate out, so do not blacklist."""
    memory = build_user_food_memory(plan_outcomes=[("r1", "skipped")])

    assert memory.skipped_dishes == {"r1": 1}
    assert memory.refused_recipe_ids == frozenset()
    assert 0 < memory.skip_score("r1") < 1


def test_skipping_the_same_dish_twice_is_a_refusal():
    memory = build_user_food_memory(
        plan_outcomes=[("r1", "skipped"), ("r1", "skipped"), ("r2", "skipped")]
    )

    assert memory.refused_recipe_ids == frozenset({"r1"})
    assert memory.skip_score("r1") == 1.0
    assert memory.skip_score("r2") < 1.0


def test_a_skip_score_never_runs_past_one():
    memory = build_user_food_memory(plan_outcomes=[("r1", "skipped")] * 20)

    assert memory.skip_score("r1") == 1.0


def test_a_dish_never_skipped_carries_no_penalty():
    memory = build_user_food_memory(
        plan_outcomes=[("r1", "skipped"), ("r1", "skipped")]
    )

    assert memory.skip_score("other") == 0.0
    assert memory.skip_score(None) == 0.0
    assert memory.skip_score("") == 0.0


def test_skips_alone_are_still_a_memory_worth_having():
    """A user who logs nothing but refuses dishes has told us something."""
    memory = build_user_food_memory(
        plan_outcomes=[("r1", "skipped"), ("r1", "skipped")]
    )

    assert memory is not EMPTY_MEMORY
    assert "Skipped 1 planned dishes more than once" in memory.prompt_text()


def test_a_skip_is_read_against_the_dish_not_its_ingredients():
    """The user may have eaten out; that says nothing about pork."""
    memory = build_user_food_memory(
        affinity_weights={11: 0.9}, plan_outcomes=[("r1", "skipped"), ("r1", "skipped")]
    )

    assert memory.taste_score([11]) > 0.5


def test_cooking_a_planned_dish_is_remembered_as_a_yes():
    memory = build_user_food_memory(
        plan_outcomes=[("r1", "logged"), ("r1", "logged"), ("r2", "skipped")]
    )

    assert memory.cooked_dishes == {"r1": 2}
    assert memory.proven_recipe_ids == frozenset({"r1"})
    assert memory.skip_score("r1") == 0.0


def test_what_the_user_eats_at_breakfast_is_not_what_they_eat_at_dinner():
    meals = [
        LoggedMealShape(dish_name="Phở gà", meal_type="breakfast", calories=400),
        LoggedMealShape(dish_name="Phở gà", meal_type="breakfast", calories=400),
        LoggedMealShape(dish_name="Thịt kho", meal_type="dinner", calories=700),
    ]

    memory = build_user_food_memory(meals=meals)

    assert memory.slot_dish_score(0, "Phở gà") == 1.0
    assert memory.slot_dish_score(0, "Thịt kho") == 0.0
    assert memory.slot_dish_score(2, "Thịt kho") == 1.0


def test_the_calorie_split_is_learned_only_when_every_slot_has_a_history():
    """A user who logs only dinner must not read as eating 100% at dinner."""
    dinner_only = [
        LoggedMealShape(dish_name="Thịt kho", meal_type="dinner", calories=700)
        for _ in range(10)
    ]

    assert build_user_food_memory(meals=dinner_only).slot_calorie_shares == ()


def test_a_big_breakfast_eater_gets_a_bigger_breakfast():
    meals = []
    for _ in range(8):
        meals.append(
            LoggedMealShape(dish_name="Xôi", meal_type="breakfast", calories=800)
        )
        meals.append(LoggedMealShape(dish_name="Cơm", meal_type="lunch", calories=500))
        meals.append(
            LoggedMealShape(dish_name="Canh", meal_type="dinner", calories=300)
        )

    shares = build_user_food_memory(meals=meals).slot_calorie_shares

    assert len(shares) == 3
    assert shares[0] > shares[1] > shares[2]
    assert abs(sum(shares) - 1.0) < 1e-9


def test_a_learned_split_never_starves_a_meal():
    """Someone who barely eats breakfast still gets some breakfast."""
    meals = []
    for _ in range(8):
        meals.append(
            LoggedMealShape(dish_name="Cà phê", meal_type="breakfast", calories=20)
        )
        meals.append(LoggedMealShape(dish_name="Cơm", meal_type="lunch", calories=900))
        meals.append(
            LoggedMealShape(dish_name="Cơm tối", meal_type="dinner", calories=900)
        )

    shares = build_user_food_memory(meals=meals).slot_calorie_shares

    assert shares[0] >= 0.09
    assert abs(sum(shares) - 1.0) < 1e-9


def test_someone_who_eats_the_same_few_dishes_tolerates_repeats():
    """Four dishes across twenty meals is a person with favourites."""
    meals = [
        LoggedMealShape(dish_name=f"Món {i % 4}", meal_type="lunch", calories=500)
        for i in range(20)
    ]

    assert build_user_food_memory(meals=meals).repeat_tolerance == 4


def test_someone_who_never_repeats_still_gets_variety():
    meals = [
        LoggedMealShape(dish_name=f"Món {i}", meal_type="lunch", calories=500)
        for i in range(20)
    ]

    assert build_user_food_memory(meals=meals).repeat_tolerance == 1


def test_a_short_log_never_changes_the_variety_rule():
    """Three meals of the same dish is not yet a habit."""
    meals = [
        LoggedMealShape(dish_name="Cơm", meal_type="lunch", calories=500)
        for _ in range(3)
    ]

    assert build_user_food_memory(meals=meals).repeat_tolerance == 1
    assert EMPTY_MEMORY.repeat_tolerance == 1


def test_repeat_tolerance_has_a_ceiling():
    """A week of one dish seven times is not a plan."""
    meals = [
        LoggedMealShape(dish_name="Cơm", meal_type="lunch", calories=500)
        for _ in range(60)
    ]

    assert build_user_food_memory(meals=meals).repeat_tolerance == 4
