"""Provider-neutral AI model purposes."""

from enum import Enum


class ModelPurpose(Enum):
    MEAL_SCAN = "meal_scan"
    FOOD_LABEL_SCAN = "food_label_scan"
    INGREDIENT_SCAN = "ingredient_scan"
    PARSE_TEXT = "parse_text"
    BARCODE = "barcode"
    MEAL_NAMES = "meal_names"
    RECIPE = "recipe"
    DISCOVERY = "discovery"
    GENERAL = "general"
    MEAL_PLAN_ADJUSTMENT = "meal_plan_adjustment"
