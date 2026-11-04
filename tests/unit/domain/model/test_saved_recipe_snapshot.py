"""Snapshot of a saved recipe held by a weekly plan slot."""

import pytest

from src.domain.model.weekly_meal_planner import (
    SavedRecipeSnapshot,
    WeeklyMealPlanSlot,
)
from src.domain.model.weekly_meal_planner.saved_recipe import (
    MAX_SAVED_RECIPE_INGREDIENTS,
    SOURCE_RECIPE_IMPORT,
    SOURCE_SUGGESTION,
)


def imported_recipe_data(**overrides):
    """suggestion_data as the recipe-import /save endpoint stores it."""
    data = {
        "id": "recipe_import_imp-1",
        "meal_name": "Chicken pho",
        "description": "Clear broth",
        "emoji": "🍜",
        "macros": {
            "calories": 999,
            "protein": 30.0,
            "carbs": 50.0,
            "fat": 10.0,
            "fiber": 4.0,
            "sugar": 3.0,
        },
        "ingredients": [
            {
                "name": "Rice noodles",
                "amount": 400.0,
                "unit": "g",
                "grams": 400.0,
                "food_reference_id": 12,
                "protein": 28.0,
                "carbs": 320.0,
                "fat": 2.0,
                "fiber": 8.0,
                "sugar": 0.0,
            },
            {
                "name": "Eggs",
                "amount": 2.0,
                "unit": "",
                "grams": 100.0,
                "food_reference_id": None,
                "protein": 12.0,
                "carbs": 1.0,
                "fat": 10.0,
            },
            {"name": "Salt", "amount": 0.0, "unit": "", "grams": None},
        ],
        "recipe_steps": [
            {"step": 1, "instruction": "Boil the broth.", "duration_minutes": 30},
            {"step": 2, "instruction": "Add noodles.", "duration_minutes": None},
        ],
        "prep_time_minutes": 45,
        "image_url": "https://images.example/pho.jpg",
        "servings": 4,
        "cuisine_type": "Vietnamese",
        "origin_country": "Vietnam",
        "language": "en",
        "recipe_import": {
            "platform": "website",
            "source_url": "https://cook.example/pho",
            "source_author": "Chef",
            "original_servings": 4,
            "servings_estimated": False,
            "total_time_minutes": 50,
        },
    }
    data.update(overrides)
    return data


def test_import_snapshot_keeps_name_macros_lines_and_time():
    snapshot = SavedRecipeSnapshot.from_saved_suggestion(
        "recipe_import_imp-1", imported_recipe_data()
    )

    assert snapshot.name == "Chicken pho"
    assert snapshot.servings == 4
    assert (snapshot.protein, snapshot.carbs, snapshot.fat, snapshot.fiber) == (
        30.0,
        50.0,
        10.0,
        4.0,
    )
    # Calories come from the formula, never the stored number.
    assert snapshot.calories == round(30 * 4 + 46 * 4 + 4 * 2 + 10 * 9)
    assert snapshot.cook_time_minutes == 50
    assert snapshot.source == SOURCE_RECIPE_IMPORT
    assert snapshot.source_url == "https://cook.example/pho"
    assert [item.name for item in snapshot.ingredients] == [
        "Rice noodles",
        "Eggs",
        "Salt",
    ]
    noodles, eggs, salt = snapshot.ingredients
    assert (noodles.amount, noodles.food_reference_id, noodles.protein) == (
        400.0,
        12,
        28.0,
    )
    assert (eggs.amount, eggs.unit, eggs.grams) == (2.0, "", 100.0)
    assert salt.amount is None
    assert [step.instruction for step in snapshot.steps] == [
        "Boil the broth.",
        "Add noodles.",
    ]


def test_snapshot_round_trips_through_stored_json():
    snapshot = SavedRecipeSnapshot.from_saved_suggestion(
        "recipe_import_imp-1", imported_recipe_data()
    )

    assert SavedRecipeSnapshot.from_dict(snapshot.to_dict()) == snapshot
    assert SavedRecipeSnapshot.from_dict(None, suggestion_id="x").suggestion_id == "x"


def test_ai_meal_idea_is_one_serving_with_prep_time_as_total():
    snapshot = SavedRecipeSnapshot.from_saved_suggestion(
        "sugg-7",
        {
            "meal_name": "Tofu bowl",
            "macros": {"calories": 400, "protein": 20, "carbs": 45, "fat": 12},
            "ingredients": [{"name": "Tofu", "amount": 150, "unit": "g"}],
            "recipe_steps": [{"step": 1, "instruction": "Fry."}],
            "prep_time_minutes": 25,
        },
    )

    assert (snapshot.servings, snapshot.source, snapshot.cook_time_minutes) == (
        1,
        SOURCE_SUGGESTION,
        25,
    )
    assert snapshot.fiber == 0.0
    assert snapshot.ingredients[0].has_macros is False


def test_normalized_projection_shape_is_read():
    # GET /v1/saved-suggestions merges normalized columns into the payload.
    snapshot = SavedRecipeSnapshot.from_saved_suggestion(
        "legacy",
        {
            "dish_name": "Legacy dish",
            "macros": {"protein_g": 10, "carbs_g": 20, "fat_g": 5},
            "ingredients": [
                {"name": "Rice", "quantity": None, "amount": 0.0, "unit": ""},
                "Pepper",
            ],
            "instructions": [{"instruction": "Cook", "duration_minutes": 5}],
        },
    )

    assert snapshot.name == "Legacy dish"
    assert (snapshot.protein, snapshot.carbs, snapshot.fat) == (10.0, 20.0, 5.0)
    assert [item.amount for item in snapshot.ingredients] == [None, None]
    assert snapshot.steps[0].duration_minutes == 5


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"meal_name": "  ", "macros": {"protein": 1}},
        {"meal_name": "No macros", "macros": {"calories": 500}},
        {"meal_name": "Negative", "macros": {"protein": -1, "carbs": 1, "fat": 1}},
        {"meal_name": "Absurd", "macros": {"protein": 1e9, "carbs": 1, "fat": 1}},
    ],
)
def test_unusable_saved_recipes_are_rejected(data):
    with pytest.raises(ValueError):
        SavedRecipeSnapshot.from_saved_suggestion("bad", data)


def test_untrusted_payload_is_bounded_and_cleaned():
    snapshot = SavedRecipeSnapshot.from_saved_suggestion(
        "big",
        {
            "meal_name": "x" * 1000,
            "macros": {"protein": "12.5", "carbs": True, "fat": "nan"},
            "image_url": "javascript:alert(1)",
            "servings": 5000,
            "ingredients": [
                {"name": f"item {index}", "amount": 1, "food_reference_id": 1.5}
                for index in range(MAX_SAVED_RECIPE_INGREDIENTS + 10)
            ],
        },
    )

    assert len(snapshot.name) == 255
    assert (snapshot.protein, snapshot.carbs, snapshot.fat) == (12.5, 0.0, 0.0)
    assert snapshot.image_url is None
    assert snapshot.servings == 100
    assert len(snapshot.ingredients) == MAX_SAVED_RECIPE_INGREDIENTS
    assert snapshot.ingredients[0].food_reference_id is None


def test_slot_holds_one_recipe_and_reports_the_client_id():
    snapshot = SavedRecipeSnapshot.from_saved_suggestion(
        "recipe_import_imp-1", imported_recipe_data()
    )
    slot = WeeklyMealPlanSlot(
        id="s",
        day_index=0,
        slot_index=0,
        recipe_id=None,
        saved_suggestion_id="recipe_import_imp-1",
        saved_recipe=snapshot,
    )

    assert slot.has_recipe and slot.recipe_ref == "recipe_import_imp-1"
    with pytest.raises(ValueError):
        WeeklyMealPlanSlot(
            id="s",
            day_index=0,
            slot_index=0,
            recipe_id="catalog-1",
            saved_suggestion_id="recipe_import_imp-1",
            saved_recipe=snapshot,
        )
    with pytest.raises(ValueError):
        WeeklyMealPlanSlot(
            id="s", day_index=0, slot_index=0, recipe_id=None, saved_suggestion_id="x"
        )
    empty = WeeklyMealPlanSlot(id="s", day_index=0, slot_index=0, recipe_id=None)
    assert not empty.has_recipe and empty.recipe_ref is None
