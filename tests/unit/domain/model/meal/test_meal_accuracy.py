"""Meal-detail accuracy fields: AI source flag, scan confidence, origin counts."""

import pytest

from src.domain.model.meal.meal_accuracy import (
    AI_GENERATED_MEAL_SOURCES,
    is_ai_generated_meal_source,
    meal_accuracy_fields,
    nutrition_source_summary,
    scan_confidence,
)


class _Item:
    def __init__(self, source_kind):
        self.source_kind = source_kind


class _Nutrition:
    def __init__(self, confidence_score=0.91, food_items=None):
        self.confidence_score = confidence_score
        self.food_items = food_items


class _Meal:
    def __init__(self, source, nutrition=None):
        self.source = source
        self.nutrition = nutrition


AI_SOURCES = ("scanner", "food_label", "prompt", "ai_suggestion")
OTHER_SOURCES = (
    None,
    "",
    "manual",
    "manual_meal",
    "manual_meal_v2",
    "food_search",
    "catalog_meal_log",
    "recommended_meal_log",
    "repeat",
    "hydration",
    "weekly_meal_planner",
    "saved_meal_suggestion",
    "Scanner",
)


@pytest.mark.parametrize("source", AI_SOURCES)
def test_ai_generated_true_for_ai_sources(source):
    assert is_ai_generated_meal_source(source) is True
    fields = meal_accuracy_fields(_Meal(source))
    assert fields["ai_generated"] is True


@pytest.mark.parametrize("source", OTHER_SOURCES)
def test_ai_generated_false_for_other_sources(source):
    assert is_ai_generated_meal_source(source) is False
    fields = meal_accuracy_fields(_Meal(source, nutrition=_Nutrition()))
    assert fields["ai_generated"] is False
    assert fields["ai_confidence"] is None


def test_ai_generated_source_set_is_exactly_the_four_ai_paths():
    assert AI_GENERATED_MEAL_SOURCES == frozenset(AI_SOURCES)


def test_scan_confidence_is_stored_score_only_for_scanner():
    nutrition = _Nutrition(confidence_score=0.87)
    assert scan_confidence(_Meal("scanner", nutrition)) == 0.87
    assert meal_accuracy_fields(_Meal("scanner", nutrition))["ai_confidence"] == 0.87


def test_scan_confidence_null_when_scanner_has_no_nutrition():
    assert scan_confidence(_Meal("scanner", None)) is None


def test_scan_confidence_keeps_a_zero_score():
    assert scan_confidence(_Meal("scanner", _Nutrition(confidence_score=0.0))) == 0.0


@pytest.mark.parametrize("source", ("food_label", "prompt", "ai_suggestion", "manual"))
def test_scan_confidence_null_when_source_is_not_scanner(source):
    meal = _Meal(source, _Nutrition(confidence_score=0.99))
    assert scan_confidence(meal) is None


def test_nutrition_source_summary_counts_item_origins():
    meal = _Meal(
        "manual",
        _Nutrition(
            food_items=[
                _Item("local"),
                _Item("local"),
                _Item("usda"),
                _Item("provider"),
                _Item("custom"),
                _Item(None),
                _Item("fatsecret"),
                _Item("unknown"),
            ]
        ),
    )
    assert nutrition_source_summary(meal) == {
        "local": 2,
        "usda": 1,
        "provider": 1,
        "custom": 1,
        "unknown": 3,
    }


def test_nutrition_source_summary_is_zero_without_items():
    assert nutrition_source_summary(_Meal("scanner", None)) == {
        "local": 0,
        "usda": 0,
        "provider": 0,
        "custom": 0,
        "unknown": 0,
    }
    assert (
        nutrition_source_summary(_Meal("prompt", _Nutrition(food_items=[])))["unknown"]
        == 0
    )
