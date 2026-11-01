from dataclasses import fields

import pytest

from src.domain.model.nutrition.micros import Micros
from src.domain.services.micronutrient_dri_targets import (
    fiber_goal_g,
    micronutrient_daily_goals,
)

_WOMAN_30 = {
    "vitamin_a": 700,
    "vitamin_d": 15,
    "vitamin_e": 6,
    "vitamin_k": 150,
    "vitamin_c": 100,
    "thiamin": 1.0,
    "riboflavin": 1.2,
    "niacin": 14,
    "vitamin_b6": 1.3,
    "folate": 400,
    "vitamin_b12": 2.4,
    "calcium": 800,
    "phosphorus": 700,
    "magnesium": 290,
    "potassium": 2000,
    "iron": 17.4,
    "zinc": 8,
    "selenium": 26,
}


def test_woman_30_gets_every_vietnam_2016_goal():
    goals = micronutrient_daily_goals(gender="female", age=30, plan_calories=1600).goals
    for key, expected in _WOMAN_30.items():
        assert goals[key] == pytest.approx(expected), key


def test_woman_30_on_1600_kcal_gets_the_three_limits_and_fiber():
    result = micronutrient_daily_goals(gender="female", age=30, plan_calories=1600)
    assert result.goals["sodium"] == 2000.0
    assert result.goals["added_sugar"] == 25.0
    assert result.goals["saturated_fat"] == 17.8
    assert result.fiber_g == 25.0


def test_goals_cover_every_micros_key():
    goals = micronutrient_daily_goals(gender="male", age=40, plan_calories=2200).goals
    assert set(goals) == {field.name for field in fields(Micros)}


def test_woman_25_uses_the_20_to_29_band():
    goals = micronutrient_daily_goals(gender="female", age=25, plan_calories=1800).goals
    assert goals["vitamin_a"] == 650
    assert goals["thiamin"] == 1.1
    assert goals["magnesium"] == 270


def test_man_30():
    result = micronutrient_daily_goals(gender="male", age=30, plan_calories=2400)
    expected = {
        "vitamin_a": 900,
        "vitamin_e": 6.5,
        "vitamin_c": 100,
        "thiamin": 1.2,
        "riboflavin": 1.4,
        "niacin": 16,
        "magnesium": 370,
        "potassium": 2500,
        "iron": 7.9,
        "zinc": 10,
        "selenium": 34,
    }
    for key, value in expected.items():
        assert result.goals[key] == pytest.approx(value), key
    assert result.fiber_g == 38.0


@pytest.mark.parametrize("diet", ["vegetarian", "vegan", " Vegan "])
def test_plant_based_diet_uses_the_10_percent_iron_column(diet):
    goals = micronutrient_daily_goals(
        gender="female", age=30, plan_calories=1600, dietary_preferences=[diet]
    ).goals
    assert goals["iron"] == 26.1


def test_other_diets_keep_the_15_percent_iron_column():
    goals = micronutrient_daily_goals(
        gender="female",
        age=30,
        plan_calories=1600,
        dietary_preferences=["keto", "classic"],
    ).goals
    assert goals["iron"] == 17.4


def test_woman_over_50_drops_to_the_no_periods_iron_row():
    goals = micronutrient_daily_goals(gender="female", age=55, plan_calories=1600).goals
    assert goals["iron"] == 6.7


def test_teen_uses_the_15_to_19_band():
    goals = micronutrient_daily_goals(gender="female", age=17, plan_calories=2000).goals
    assert goals["calcium"] == 1000
    assert goals["iron"] == 19.8


def test_saturated_fat_scales_with_plan_calories():
    goals = micronutrient_daily_goals(gender="male", age=30, plan_calories=2700).goals
    assert goals["saturated_fat"] == 30.0


def test_unknown_sex_uses_the_female_column():
    goals = micronutrient_daily_goals(gender="other", age=30, plan_calories=2000).goals
    assert goals["iron"] == 17.4


def test_missing_profile_defaults_to_a_woman_of_30_on_2000_kcal():
    result = micronutrient_daily_goals(gender=None, age=None, plan_calories=0)
    assert result.goals["iron"] == 17.4
    assert result.goals["potassium"] == 2000.0
    assert result.goals["saturated_fat"] == 22.2
    assert result.fiber_g == 25.0


def test_fiber_goal_is_fixed_by_sex_and_age():
    assert fiber_goal_g("female", 30) == 25.0
    assert fiber_goal_g("male", 30) == 38.0
    assert fiber_goal_g("male", 60) == 30.0
    assert fiber_goal_g(None, None) == 25.0


def test_goals_carry_the_upper_limits():
    limits = micronutrient_daily_goals(
        gender="female", age=30, plan_calories=1600
    ).upper_limits
    assert limits["iron"].value == 45.0
    assert "potassium" not in limits
