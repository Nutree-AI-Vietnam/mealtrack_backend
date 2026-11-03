"""What the planner remembers about how one person eats."""

from src.domain.model.weekly_meal_planner.user_food_memory import (
    CONFIDENT_MEAL_COUNT,
    EMPTY_MEMORY,
    LoggedMealShape,
    UserFoodMemory,
    build_user_food_memory,
)
from src.domain.services.weekly_meal_planner.meal_locale import VIETNAM


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
