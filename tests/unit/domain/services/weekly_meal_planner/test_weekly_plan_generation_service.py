from decimal import Decimal

import pytest

from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.model.weekly_meal_planner import (
    WEEKLY_DAYS,
    WEEKLY_PLAN_SLOT_COUNT,
    WEEKLY_SLOTS_PER_DAY,
    WeeklyMealPlanPreferences,
)
from src.domain.model.weekly_meal_planner.user_food_memory import (
    EMPTY_MEMORY,
    UserFoodMemory,
)
from src.domain.services.weekly_meal_planner import WeeklyPlanGenerationService


def _meal(
    meal_id: str,
    name: str,
    meal_types=("lunch", "dinner"),
    *,
    popularity=1,
    carbs="40",
):
    return CatalogMeal(
        id=meal_id,
        catalog_key=meal_id,
        content_hash="a" * 64,
        name=name,
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("30"),
        carbs_g=Decimal(carbs),
        fat_g=Decimal("10"),
        fiber_g=Decimal("5"),
        meal_types=meal_types,
        popularity_rank=popularity,
        ingredients=(
            CatalogMealIngredient(
                food_reference_id=1,
                display_name=name,
                quantity=Decimal("100"),
                unit="g",
            ),
        ),
    )


def test_generation_covers_all_weekly_coordinates_deterministically():
    meals = [_meal("tofu", "Tofu", meal_types=("breakfast", "lunch", "dinner"))]
    preferences = WeeklyMealPlanPreferences(diet="vegetarian")

    first = WeeklyPlanGenerationService().generate(
        meals,
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=preferences,
    )
    second = WeeklyPlanGenerationService().generate(
        meals,
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=preferences,
    )

    assert first == second
    assert len(first) == WEEKLY_PLAN_SLOT_COUNT
    assert {(slot.day_index, slot.slot_index) for slot in first} == {
        (day, slot)
        for day in range(WEEKLY_DAYS)
        for slot in range(WEEKLY_SLOTS_PER_DAY)
    }
    assert {slot.recipe_id for slot in first} == {"tofu"}


def test_breakfast_uses_a_simple_dish_when_salmon_is_also_eligible():
    generated = WeeklyPlanGenerationService().generate(
        [
            _meal("salmon", "Cháo cá hồi", meal_types=("breakfast",)),
            _meal("eggs", "Trứng ốp la", meal_types=("breakfast",)),
            _meal("tofu", "Tofu", meal_types=("lunch",)),
            _meal("soup", "Canh rau", meal_types=("dinner",)),
        ],
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=1800,
        preferences=WeeklyMealPlanPreferences(),
    )

    breakfasts = {slot.recipe_id for slot in generated if slot.slot_index == 0}
    assert breakfasts == {"eggs"}


def test_generation_assigns_each_meal_type_to_its_slot():
    generated = WeeklyPlanGenerationService().generate(
        [
            _meal("oats", "Oats", meal_types=("breakfast",)),
            _meal("tofu", "Tofu", meal_types=("lunch",)),
            _meal("soup", "Soup", meal_types=("dinner",)),
        ],
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=1800,
        preferences=WeeklyMealPlanPreferences(),
    )

    by_slot = {
        slot.slot_index: slot.recipe_id for slot in generated if slot.day_index == 0
    }
    assert by_slot == {0: "oats", 1: "tofu", 2: "soup"}
    assert len(generated) == WEEKLY_PLAN_SLOT_COUNT


def test_generation_leaves_slots_empty_when_hard_preference_has_no_match():
    meals = [_meal("chicken", "Chicken Bowl")]
    preferences = WeeklyMealPlanPreferences(diet="vegetarian")

    result = WeeklyPlanGenerationService().generate(
        meals,
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=preferences,
    )

    assert len(result) == WEEKLY_PLAN_SLOT_COUNT
    assert all(slot.recipe_id is None for slot in result)


@pytest.mark.parametrize(
    "name",
    [
        "Rosita cocktail",
        "Ly đá chanh bạc hà",
        "Sữa Macca",
        "Sữa đậu xanh lá dứa",
        "Chocolate tofu pudding",
        "French toast with cinnamon",
        "Bánh khoai mỡ chiên",
        "Bánh tuyết thiên sứ",
        "Bắp rang bơ",
        "Latte hoa đậu biếc",
        "Soda blue ocean",
        "Bánh con sùng nước cốt dừa",
    ],
)
def test_generation_excludes_beverages_and_desserts_from_main_meals(name):
    generated = WeeklyPlanGenerationService().generate(
        [_meal("not-a-main-meal", name)],
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=WeeklyMealPlanPreferences(),
    )

    assert all(slot.recipe_id is None for slot in generated)


@pytest.mark.parametrize(
    "name",
    [
        "Mực một nắng",
        "Tép muối xổi",
        "Sườn chiên sả ớt",
        "Cải thìa xào dầu hào",
        "Tofu with shrimp",
    ],
)
def test_vegetarian_generation_excludes_all_catalogued_seafood(name):
    generated = WeeklyPlanGenerationService().generate(
        [_meal("seafood", name)],
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=WeeklyMealPlanPreferences(diet="vegetarian"),
    )

    assert all(slot.recipe_id is None for slot in generated)


def _week(meals, **kwargs):
    return WeeklyPlanGenerationService().generate(
        meals,
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=WeeklyMealPlanPreferences(),
        **kwargs,
    )


def _compact_week(meals, basket_ids, **kwargs):
    return WeeklyPlanGenerationService().generate_compact(
        meals,
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=WeeklyMealPlanPreferences(),
        basket_ids=basket_ids,
        **kwargs,
    )


def _grid(week):
    return [
        [slot.recipe_id for slot in week if slot.day_index == day] for day in range(7)
    ]


def _mixed_pool():
    """Six breakfasts and 30 mains; every main shares one of 12 ingredients."""
    basket_ids = {f"breakfast-{i}": frozenset({500 + i, 600 + i}) for i in range(6)} | {
        f"main-{i:02d}": frozenset({3 * i, 3 * i + 1, (3 * i + 2) % 12})
        for i in range(30)
    }
    meals = [
        _meal(meal_id, meal_id, meal_types=("breakfast",), carbs="75")
        for meal_id in basket_ids
        if meal_id.startswith("breakfast")
    ] + [
        _meal(meal_id, meal_id, carbs="137.5")
        for meal_id in basket_ids
        if meal_id.startswith("main")
    ]
    return meals, basket_ids


def test_compact_week_cooks_ten_dishes_and_eats_each_dinner_again_at_lunch():
    meals, basket_ids = _mixed_pool()

    plain = _grid(_week(meals))
    compact = _grid(_compact_week(meals, basket_ids))

    breakfasts = [day[0] for day in compact]
    assert len(set(breakfasts)) == 2
    assert breakfasts == [breakfasts[day % 2] for day in range(7)]
    for day in range(1, 7):
        assert compact[day][1] == compact[day - 1][2]
        assert compact[day][2] != compact[day][1]
    dinners = [day[2] for day in compact]
    assert len(set(dinners)) == 7
    assert len({recipe for day in compact for recipe in day}) == 10
    assert len({recipe for day in plain for recipe in day}) > 10


def test_compact_week_prefers_dishes_sharing_fresh_ingredients():
    # Sixteen dishes share one set of fresh ingredients; twenty need their own.
    basket_ids = {f"shared-{i:02d}": frozenset({1, 2, 3}) for i in range(16)} | {
        f"own-{i:02d}": frozenset({100 + 3 * i, 101 + 3 * i, 102 + 3 * i})
        for i in range(20)
    }
    meals = [
        _meal(meal_id, meal_id, meal_types=("breakfast", "lunch", "dinner"))
        for meal_id in basket_ids
    ]

    def fresh_items(week):
        return set().union(*(basket_ids[slot.recipe_id] for slot in week))

    compact = _compact_week(meals, basket_ids)
    cooked = {slot.recipe_id for slot in compact}

    assert len(cooked) == 10
    assert all(recipe.startswith("shared") for recipe in cooked)
    assert fresh_items(compact) == {1, 2, 3}


def test_compact_week_never_trades_away_the_calorie_band():
    # Lunch targets 750 kcal at 2000 kcal a day; meals within 112.5 kcal of it
    # share a band. These macros give 200 + 4 * carbs kcal.
    meals = [
        _meal("anchor", "Anchor", meal_types=("lunch",), carbs="137.5"),
        _meal("closer-new", "Closer new", meal_types=("lunch",), carbs="140"),
        _meal("near-reuse", "Near reuse", meal_types=("lunch",), carbs="160"),
        _meal("far-reuse", "Far reuse", meal_types=("lunch",), carbs="197.5"),
    ]
    basket_ids = {
        "anchor": frozenset({1, 2, 3}),
        "closer-new": frozenset({4, 5, 6}),
        "near-reuse": frozenset({1, 2, 3}),
        "far-reuse": frozenset({1, 2, 3}),
    }

    lunches = [day[1] for day in _grid(_compact_week(meals, basket_ids))][:4]

    assert lunches == ["anchor", "near-reuse", "closer-new", "far-reuse"]


def test_a_meal_with_unknown_ingredients_does_not_look_free():
    meals = [
        _meal("anchor", "Anchor", meal_types=("lunch",), carbs="137.5"),
        _meal("unknown", "Unknown", meal_types=("lunch",), carbs="137.5"),
        _meal("one-new", "One new", meal_types=("lunch",), carbs="137.5"),
    ]
    basket_ids = {"anchor": frozenset({1, 2}), "one-new": frozenset({1, 9})}

    lunches = [day[1] for day in _grid(_compact_week(meals, basket_ids))][:3]

    assert lunches.index("one-new") < lunches.index("unknown")


def test_among_new_dishes_a_dinner_that_can_be_lunch_wins():
    # "dinner-only" sits closer to the calorie target, but cannot be eaten
    # again at lunch.
    basket_ids = {"both": frozenset({1}), "dinner-only": frozenset({1})}
    meals = [
        _meal("both", "Both", meal_types=("lunch", "dinner"), carbs="150"),
        _meal("dinner-only", "Dinner only", meal_types=("dinner",), carbs="137.5"),
        _meal("lunch-only", "Lunch only", meal_types=("lunch",)),
    ]

    week = _grid(_compact_week(meals, basket_ids, fixed={(0, 1): "lunch-only"}))

    assert week[0][2] == "both"
    assert week[1][1] == "both"


def test_an_unused_dinner_beats_repeating_one_that_could_be_lunch():
    # Day 0 lunch cooks "x". On day 1, dinner could repeat "x" (it fits lunch)
    # or cook the dinner-only dish not used yet.
    basket_ids = {"x": frozenset({1}), "y": frozenset({1}), "z": frozenset({1})}
    meals = [
        _meal("bread", "Bread", meal_types=("breakfast",)),
        _meal("x", "X", meal_types=("lunch", "dinner")),
        _meal("lunch-only", "Lunch only", meal_types=("lunch",)),
        _meal("y", "Y", meal_types=("dinner",)),
        _meal("z", "Z", meal_types=("dinner",)),
    ]

    week = _grid(_compact_week(meals, basket_ids))

    assert week[0][1] == "x"
    assert {week[0][2], week[1][2]} == {"y", "z"}


def test_logged_meals_stay_and_a_logged_dinner_feeds_the_next_lunch():
    meals, basket_ids = _mixed_pool()
    fixed = {(0, 0): "breakfast-3", (0, 2): "main-29", (1, 1): "main-11"}

    compact = _grid(_compact_week(meals, basket_ids, fixed=fixed))

    assert compact[0][0] == "breakfast-3"
    assert compact[0][2] == "main-29"
    assert compact[1][1] == "main-11"
    assert compact[2][1] == compact[1][2]
    assert compact[2][0] == "breakfast-3"


def test_a_logged_dinner_the_user_can_no_longer_eat_is_not_repeated():
    meals, basket_ids = _mixed_pool()
    vegetarian = WeeklyMealPlanPreferences(diet="vegetarian")

    compact = WeeklyPlanGenerationService().generate_compact(
        meals,
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=vegetarian,
        basket_ids=basket_ids,
        fixed={(0, 2): "chicken-curry"},
    )

    assert _grid(compact)[1][1] != "chicken-curry"


def _varied_pool():
    """Sixteen breakfasts and 40 mains, each a few calories apart."""
    return [
        _meal(
            f"breakfast-{i:02d}",
            f"breakfast-{i:02d}",
            ("breakfast",),
            carbs=str(40 + i),
        )
        for i in range(16)
    ] + [
        _meal(f"main-{i:02d}", f"main-{i:02d}", carbs=str(60 + 3 * i))
        for i in range(40)
    ]


def _next_week(meals, **kwargs):
    return WeeklyPlanGenerationService().generate(
        meals,
        user_id="user-1",
        week_start_date="2026-09-28",
        daily_calories=2000,
        preferences=WeeklyMealPlanPreferences(),
        **kwargs,
    )


def _recipes(week):
    return {slot.recipe_id for slot in week if slot.recipe_id}


def test_next_week_is_not_a_copy_of_last_week():
    meals = _varied_pool()
    this_week = _week(meals)

    # The calorie gap decides before the week's tie-break, so on its own the
    # same profile and catalog plan the same week again.
    assert _grid(_next_week(meals)) == _grid(this_week)

    next_week = _next_week(meals, last_week=frozenset(_recipes(this_week)))

    assert not _recipes(next_week) & _recipes(this_week)
    assert all(slot.recipe_id for slot in next_week)


def test_a_small_catalog_still_fills_the_week_from_last_weeks_dishes():
    meals = [
        _meal("breakfast-only", "breakfast-only", ("breakfast",)),
        *(_meal(f"main-{i}", f"main-{i}", carbs=str(60 + 3 * i)) for i in range(3)),
    ]
    this_week = _week(meals)

    next_week = _next_week(meals, last_week=frozenset(_recipes(this_week)))

    assert all(slot.recipe_id for slot in next_week)
    assert _recipes(next_week) == _recipes(this_week)


def test_a_compact_next_week_cooks_new_dishes_and_keeps_its_leftovers():
    meals, basket_ids = _mixed_pool()
    this_week = _compact_week(meals, basket_ids)

    next_week = WeeklyPlanGenerationService().generate_compact(
        meals,
        user_id="user-1",
        week_start_date="2026-09-28",
        daily_calories=2000,
        preferences=WeeklyMealPlanPreferences(),
        basket_ids=basket_ids,
        last_week=frozenset(_recipes(this_week)),
    )

    assert not _recipes(next_week) & _recipes(this_week)
    grid = _grid(next_week)
    for day in range(1, 7):
        assert grid[day][1] == grid[day - 1][2]
    assert len({day[0] for day in grid}) == 2


def _basket_size(week, basket_ids) -> int:
    return len(
        set().union(*(basket_ids.get(slot.recipe_id, frozenset()) for slot in week))
    )


def _sharing_pool():
    """Dishes that fit the slot's calories exactly but share no ingredients,
    against dishes a little further off that all draw on five ingredients."""
    basket_ids = {f"breakfast-{i}": frozenset({900 + i, 950 + i}) for i in range(2)}
    basket_ids |= {
        f"exotic-{i:02d}": frozenset({100 + 3 * i, 101 + 3 * i, 102 + 3 * i})
        for i in range(12)
    }
    basket_ids |= {
        f"shared-{i:02d}": frozenset({1 + i % 5, 1 + (i + 1) % 5, 1 + (i + 2) % 5})
        for i in range(12)
    }
    meals = [
        _meal(meal_id, meal_id, meal_types=("breakfast",), carbs="75")
        for meal_id in basket_ids
        if meal_id.startswith("breakfast")
    ]
    # 760 kcal against a 750 kcal slot target: the closest fit.
    meals += [
        _meal(meal_id, meal_id, carbs="137.5")
        for meal_id in basket_ids
        if meal_id.startswith("exotic")
    ]
    # 610 kcal: a whole calorie band further away, so only a basket budget
    # makes the planner prefer these.
    meals += [
        _meal(meal_id, meal_id, carbs="100")
        for meal_id in basket_ids
        if meal_id.startswith("shared")
    ]
    return meals, basket_ids


def test_a_basket_target_buys_fewer_fresh_ingredients():
    meals, basket_ids = _sharing_pool()

    free = _compact_week(meals, basket_ids)
    budgeted = _compact_week(meals, basket_ids, basket_target=12)

    assert _basket_size(free, basket_ids) > 18
    assert _basket_size(budgeted, basket_ids) <= 12
    # The dishes it picks are the ones that shop alike.
    assert (
        sum(1 for slot in budgeted if (slot.recipe_id or "").startswith("shared")) >= 10
    )
    # Every slot still gets a dish: the target is a goal, not a hard rule.
    assert all(slot.recipe_id is not None for slot in budgeted)


def test_a_bigger_target_buys_more_and_fits_calories_better():
    meals, basket_ids = _sharing_pool()

    small = _compact_week(meals, basket_ids, basket_target=12)
    large = _compact_week(meals, basket_ids, basket_target=20)

    assert _basket_size(small, basket_ids) < _basket_size(large, basket_ids)


def test_without_a_target_calories_still_lead():
    meals, basket_ids = _sharing_pool()

    week = _compact_week(meals, basket_ids)

    # Every main is the closest fit to its slot, whatever it costs the basket.
    assert all(
        slot.recipe_id.startswith(("breakfast", "exotic"))
        for slot in week
        if slot.recipe_id
    )


def test_a_budgeted_week_is_stable_for_the_same_inputs():
    meals, basket_ids = _sharing_pool()

    assert _grid(_compact_week(meals, basket_ids, basket_target=12)) == _grid(
        _compact_week(meals, basket_ids, basket_target=12)
    )


def test_a_budget_still_fills_a_week_whose_dishes_share_nothing():
    basket_ids = {f"breakfast-{i}": frozenset({900 + i}) for i in range(2)}
    basket_ids |= {f"main-{i:02d}": frozenset({100 + i}) for i in range(12)}
    meals = [
        _meal(meal_id, meal_id, meal_types=("breakfast",), carbs="75")
        for meal_id in basket_ids
        if meal_id.startswith("breakfast")
    ] + [
        _meal(meal_id, meal_id, carbs="137.5")
        for meal_id in basket_ids
        if meal_id.startswith("main")
    ]

    week = _compact_week(meals, basket_ids, basket_target=3)

    assert all(slot.recipe_id is not None for slot in week)


def _meal_with_ingredient(meal_id: str, name: str, food_reference_id: int):
    """Two dishes that fit a day identically but shop differently."""
    return CatalogMeal(
        id=meal_id,
        catalog_key=meal_id,
        content_hash="a" * 64,
        name=name,
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("30"),
        carbs_g=Decimal("40"),
        fat_g=Decimal("10"),
        fiber_g=Decimal("5"),
        meal_types=("breakfast", "lunch", "dinner"),
        popularity_rank=1,
        ingredients=(
            CatalogMealIngredient(
                food_reference_id=food_reference_id,
                display_name=name,
                quantity=Decimal("100"),
                unit="g",
            ),
        ),
    )


def _generated(meals, memory):
    return WeeklyPlanGenerationService().generate(
        meals,
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=WeeklyMealPlanPreferences(),
        memory=memory,
    )


def test_the_week_leans_toward_food_the_user_already_eats():
    """Six dishes fit the day alike; the week should favour the familiar ones.

    A day holds three slots and the planner avoids repeating a dish within
    one, so it cannot serve only the liked group - but it should reach for
    it first.
    """
    liked = [_meal_with_ingredient(f"liked-{i}", f"Liked {i}", 11) for i in range(3)]
    strange = [
        _meal_with_ingredient(f"strange-{i}", f"Strange {i}", 22) for i in range(3)
    ]
    meals = liked + strange

    week = _generated(meals, UserFoodMemory(liked_ingredients={11: 1.0}))
    served_liked = sum(
        1 for slot in week if (slot.recipe_id or "").startswith("liked-")
    )

    assert served_liked > len(week) / 2


def test_a_user_with_no_memory_gets_the_week_they_got_before():
    """A cold start must not change how anything is planned."""
    meals = [
        _meal_with_ingredient("familiar", "Familiar", 11),
        _meal_with_ingredient("stranger", "Stranger", 22),
    ]

    with_default = WeeklyPlanGenerationService().generate(
        meals,
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=WeeklyMealPlanPreferences(),
    )
    with_empty = _generated(meals, EMPTY_MEMORY)

    assert with_default == with_empty


def test_taste_never_outvotes_the_day_the_plan_has_to_hit():
    """Liking a dish is worth 40 kcal of fit, not 400.

    The liked dish here misses the slot by hundreds of calories, so the week
    comes out exactly as it would for a user the planner knows nothing about.
    """
    close = [_meal_with_ingredient(f"close-{i}", f"Close {i}", 22) for i in range(3)]
    liked_but_wrong = CatalogMeal(
        **{
            **_meal_with_ingredient("liked", "Liked", 11).__dict__,
            "protein_g": Decimal("120"),
            "carbs_g": Decimal("200"),
            "fat_g": Decimal("60"),
        }
    )
    meals = [*close, liked_but_wrong]

    with_taste = _generated(meals, UserFoodMemory(liked_ingredients={11: 1.0}))
    without = _generated(meals, EMPTY_MEMORY)

    assert with_taste == without
