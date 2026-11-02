"""The + panel's sentence split: kinds, the supplements switch, food routing, clamps."""

from datetime import time

import pytest

from src.app.schemas.meal_schemas import ParsedFoodItemDto
from src.app.services.describe_any_items import (
    ALL_KINDS,
    dish_name,
    meal_items,
    meal_type_at,
    plan_sentence,
)


def _parse(**parts):
    return {
        "food": parts.get("food"),
        "drinks": parts.get("drinks", []),
        "activities": parts.get("activities", []),
        "supplements": parts.get("supplements", []),
    }


TEA = {"catalog_id": "tea", "name": "trà đá", "servings": 1}
RUN = {"catalog_id": "running", "name": "chạy", "duration_min": 30}
D3 = {"name": "D3", "words": "1 viên D3", "units": 1, "list_ref": "s1"}
WHEY = {"name": "whey", "words": "1 muỗng whey", "product_kind": "protein_powder"}


def test_every_kind_is_planned_when_all_are_asked_for():
    plan = plan_sentence(
        _parse(food="phở bò", drinks=[TEA], activities=[RUN], supplements=[D3]),
        ALL_KINDS,
        supplements_on=True,
    )

    assert plan.food_text == "phở bò"
    assert [drink.drink_id for drink in plan.drinks] == ["tea"]
    assert [activity.activity_id for activity in plan.activities] == ["running"]
    assert [(s.name, s.list_ref, s.units) for s in plan.supplements] == [
        ("D3", "s1", 1.0)
    ]
    assert plan.skipped == []


def test_kinds_not_asked_for_are_skipped_not_saved():
    plan = plan_sentence(
        _parse(food="phở", drinks=[TEA], activities=[RUN], supplements=[D3]),
        frozenset({"supplement"}),
        supplements_on=True,
    )

    assert plan.food_text is None and plan.drinks == [] and plan.activities == []
    assert [s.name for s in plan.supplements] == ["D3"]
    assert {(s["kind"], s["reason"]) for s in plan.skipped} == {
        ("food", "not_requested"),
        ("drink", "not_requested"),
        ("activity", "not_requested"),
    }


def test_supplements_wait_for_their_switch():
    plan = plan_sentence(
        _parse(food="phở", supplements=[D3]), ALL_KINDS, supplements_on=False
    )

    assert plan.food_text == "phở" and plan.supplements == []
    assert plan.skipped == [
        {"kind": "supplement", "title": "D3", "reason": "supplements_unavailable"}
    ]


def test_protein_powder_joins_the_food():
    plan = plan_sentence(
        _parse(food="cơm tấm", supplements=[WHEY]), ALL_KINDS, supplements_on=True
    )

    assert plan.food_text == "cơm tấm, 1 muỗng whey"
    assert plan.supplements == []


def test_protein_powder_is_skipped_when_food_is_not_asked_for():
    plan = plan_sentence(
        _parse(supplements=[WHEY]), frozenset({"supplement"}), supplements_on=True
    )

    assert plan.food_text is None
    assert plan.skipped == [
        {"kind": "food", "title": "whey", "reason": "not_requested"}
    ]


@pytest.mark.parametrize(
    ("units", "planned"), [(None, None), (0, None), (-2, None), (2, 2.0), (500, 20.0)]
)
def test_units_are_clamped_or_left_to_the_list(units, planned):
    plan = plan_sentence(
        _parse(supplements=[{**D3, "units": units}]), ALL_KINDS, supplements_on=True
    )

    assert plan.supplements[0].units == planned


def test_an_estimate_keeps_only_usable_rows():
    estimate = {
        "form": "softgel",
        "nutrients": [
            {"nutrient": "vitamin_d", "amount": 1000, "unit": "IU"},
            {"nutrient": "zinc", "amount": float("nan"), "unit": "mg"},
            {"nutrient": "iron", "amount": -1, "unit": "mg"},
            {"nutrient": "calcium", "amount": 200, "unit": " "},
        ],
    }
    plan = plan_sentence(
        _parse(supplements=[{"name": "D3", "words": "", "estimate": estimate}]),
        ALL_KINDS,
        supplements_on=True,
    )

    [item] = plan.supplements
    assert item.form == "softgel" and item.words == "D3"
    assert [(row.nutrient, row.amount, row.unit) for row in item.rows] == [
        ("vitamin_d", 1000.0, "IU")
    ]


def test_nameless_items_are_dropped_and_counted():
    plan = plan_sentence(
        _parse(supplements=[{"name": "  ", "words": "x"}], drinks=[{"name": ""}]),
        ALL_KINDS,
        supplements_on=True,
    )

    assert plan.supplements == [] and plan.drinks == []
    assert plan.dropped == 2


def _parsed(**fields) -> ParsedFoodItemDto:
    base = {
        "name": "Phở bò",
        "quantity": 1,
        "unit": "tô",
        "protein": 27,
        "carbs": 45,
        "fat": 11,
        "protein_per_100g": 6.0,
        "carbs_per_100g": 10.0,
        "fat_per_100g": 2.5,
        "calories_per_100g": 86.5,
        "allowed_units": [{"unit": "tô", "gram_weight": 450, "description": "1 tô"}],
        "source_snapshot": {"basis": "100g"},
    }
    return ParsedFoodItemDto(**{**base, **fields})


def test_parsed_food_saves_as_prepared_items_by_origin():
    custom, local, usda, provider, unlabelled = meal_items(
        [
            _parsed(origin="custom"),
            _parsed(food_reference_id=7, fdc_id=99),
            _parsed(fdc_id=99),
            _parsed(
                origin="provider",
                source_namespace="fatsecret",
                source_food_id="fs-1",
                fdc_id=99,
            ),
            _parsed(source_namespace="fatsecret", source_food_id="fs-2"),
        ]
    )

    assert custom.origin == "custom" and custom.fdc_id is None
    assert (local.origin, local.food_reference_id, local.fdc_id) == ("local", 7, None)
    assert (usda.origin, usda.fdc_id) == ("usda", 99)
    assert (provider.origin, provider.source_food_id, provider.fdc_id) == (
        "provider",
        "fs-1",
        None,
    )
    assert unlabelled.origin == "provider"  # the app's rule without an origin
    assert custom.nutrition_contract_version == "2"
    assert custom.custom_nutrition.protein_per_100g == 6.0
    assert custom.custom_nutrition.fiber_per_100g == 0.0
    assert custom.source_snapshot == {"basis": "100g"}
    assert custom.unit == "tô" and custom.allowed_units[0]["gram_weight"] == 450


def test_dish_name_lists_the_foods():
    assert dish_name([_parsed(), _parsed(name="Quẩy")]) == "Phở bò, Quẩy"
    assert dish_name([_parsed(name="x" * 300)]) == "x" * 200


@pytest.mark.parametrize(
    ("clock", "meal_type"),
    [
        (time(4, 59), "snack"),
        (time(5), "breakfast"),
        (time(10), "lunch"),
        (time(14), "snack"),
        (time(17), "dinner"),
        (time(22), "snack"),
    ],
)
def test_meal_type_follows_the_apps_clock_rule(clock, meal_type):
    assert meal_type_at(clock) == meal_type


def test_a_powder_already_in_the_food_is_not_counted_twice():
    plan = plan_sentence(
        _parse(food="phở bò, 1 muỗng whey", supplements=[WHEY]),
        ALL_KINDS,
        supplements_on=True,
    )

    assert plan.food_text == "phở bò, 1 muỗng whey"


def test_items_past_the_limits_are_counted_as_dropped():
    plan = plan_sentence(
        _parse(supplements=[{**D3, "name": f"S{n}"} for n in range(20)]),
        ALL_KINDS,
        supplements_on=True,
    )

    assert len(plan.supplements) == 8 and plan.dropped == 12


@pytest.mark.parametrize("amount", [100_001, 1e308])
def test_an_absurd_guess_never_reaches_the_unit_rules(amount):
    estimate = {
        "form": "tablet",
        "nutrients": [{"nutrient": "vitamin_b12", "amount": amount, "unit": "mg"}],
    }
    plan = plan_sentence(
        _parse(supplements=[{"name": "B12", "words": "B12", "estimate": estimate}]),
        ALL_KINDS,
        supplements_on=True,
    )

    assert plan.supplements[0].rows == ()
