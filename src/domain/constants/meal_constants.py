"""
Constants for meal-related domain operations.

This module centralizes all meal-related constants, magic numbers,
and configuration values used throughout the domain layer.
"""


class MealDistribution:
    """Constants for meal calorie distribution."""

    # Standard distribution percentages
    BREAKFAST_PERCENT = 0.25
    LUNCH_PERCENT = 0.35
    DINNER_PERCENT = 0.30
    SNACK_PERCENT = 0.10

    # Calorie thresholds
    MIN_CALORIES_FOR_SNACK = 1800

    # With snack adjustments
    BREAKFAST_WITH_SNACK = 0.225  # 25% * 0.9
    LUNCH_WITH_SNACK = 0.315  # 35% * 0.9
    DINNER_WITH_SNACK = 0.27  # 30% * 0.9


class MealDefaults:
    """Default values for user-facing meal fields."""

    UNNAMED_FOOD_NAME = "Unnamed Food"


class NutritionConstants:
    """Constants for nutrition calculations."""

    # Calorie per gram of macronutrients
    CALORIES_PER_GRAM_PROTEIN = 4
    CALORIES_PER_GRAM_CARBS = 4
    CALORIES_PER_GRAM_FAT = 9

    # Validation tolerances
    CALORIE_TOLERANCE_PERCENT = 0.20  # 20% tolerance for calorie validation
    TOTAL_CALORIE_TOLERANCE_PERCENT = 0.05  # 5% tolerance for total calories

    # Default confidence scores
    DEFAULT_FOOD_CONFIDENCE = 1.0
    MIN_CONFIDENCE_THRESHOLD = 0.5


class PortionUnits:
    """Valid units for portion sizes."""

    WEIGHT_UNITS = ["g", "oz", "kg", "lb"]
    VOLUME_UNITS = ["ml", "l", "cup", "tbsp", "tsp", "fl oz"]
    COUNT_UNITS = ["piece", "serving", "slice", "unit"]

    ALL_UNITS = WEIGHT_UNITS + VOLUME_UNITS + COUNT_UNITS

    # Conversion factors to grams (approximate)
    TO_GRAMS = {
        "g": 1,
        "oz": 28.35,
        "kg": 1000,
        "lb": 453.59,
        "cup": 240,  # Approximate, varies by ingredient
        "tbsp": 15,  # Approximate
        "tsp": 5,  # Approximate
    }


class GPTPromptConstants:
    """Constants for GPT prompts."""

    # Response format version
    RESPONSE_FORMAT_VERSION = "1.0"

    # Confidence thresholds
    HIGH_CONFIDENCE = 0.8
    MEDIUM_CONFIDENCE = 0.6
    LOW_CONFIDENCE = 0.4

    # Token limits
    MAX_OUTPUT_TOKENS = 2000
    MAX_PROMPT_LENGTH = 4000


class MealPlanningConstants:
    """Constants for meal planning."""

    # Cooking time defaults (minutes)
    DEFAULT_WEEKDAY_COOKING_TIME = 30
    DEFAULT_WEEKEND_COOKING_TIME = 60
    MAX_SNACK_PREP_TIME = 15

    # Meal variety
    MIN_DAYS_BEFORE_REPEAT = 3
    MAX_CUISINE_REPEATS_PER_WEEK = 2

    # Plan duration
    WEEKLY_PLAN_DAYS = 7
    DAILY_PLAN_DAYS = 1


class TDEEConstants:
    """Constants for TDEE calculations."""

    # Job type base multipliers (NEAT component)
    JOB_TYPE_MULTIPLIERS = {
        "desk": 1.2,  # Sitting most of day
        "on_feet": 1.4,  # Standing/walking (retail, teaching)
        "physical": 1.6,  # Manual labor (construction, warehouse)
    }

    # Goal adjustments (calories)
    # 300 deficit = ~0.3 kg/week, sustainable for small users
    CUTTING_DEFICIT = 300
    BULKING_SURPLUS = 300  # 300 calorie surplus (lean bulk)
    # MAINTENANCE_ADJUSTMENT removed - use RECOMP_ADJUSTMENT instead
    RECOMP_ADJUSTMENT = 0  # No calorie adjustment for recomposition

    # Protein targets (g per kg body weight), goal-scoped.
    # Cut is highest to bias lean-mass retention in a deficit; bulk is lowest
    # because surplus calories already support muscle gain.
    PROTEIN_PER_KG = {
        "cut": 1.8,
        "recomp": 1.7,
        "bulk": 1.6,
    }

    # Training-level table kept for API compatibility; rates match PROTEIN_PER_KG
    # (no progressive bump by experience).
    # Beginner: <1 year consistent training
    # Intermediate: 1-3 years consistent training
    # Advanced: 3+ years consistent training
    PROTEIN_PER_KG_BY_TRAINING = {
        "cut": {"beginner": 1.8, "intermediate": 1.8, "advanced": 1.8},
        "recomp": {"beginner": 1.7, "intermediate": 1.7, "advanced": 1.7},
        "bulk": {"beginner": 1.6, "intermediate": 1.6, "advanced": 1.6},
    }

    # Fat intake: 0.5-1.5 g/kg for hormone production
    # Dorgan 1996: below 20% calories reduces testosterone
    # Kerksick 2018 ISSN position: minimum 20% calories
    FAT_PER_KG = {
        "cut": 0.8,
        "recomp": 0.9,
        "bulk": 1.0,
    }

    # Minimum fat as percentage of total calories (dual-gate with g/kg)
    # Uses max(g/kg, % calories) to ensure hormone function
    # Dorgan 1996: <20% cal reduces testosterone; Kerksick 2018 ISSN position
    FAT_MIN_PERCENT = {
        "cut": 0.20,
        "recomp": 0.25,
        "bulk": 0.25,
    }

    # Performance floor for resistance training (Burke 2011, Escobar 2016)
    # Informational only — used in code comments, not enforced at runtime
    MIN_CARBS_PER_KG = 2.5

    # Min/max bounds for safety
    MIN_PROTEIN_G = 60  # Minimum daily protein
    MAX_PROTEIN_G = 300  # Maximum daily protein
    MIN_FAT_G = 40  # Minimum daily fat for hormone function
    MAX_FAT_G = 150  # Maximum daily fat
    MIN_CARBS_G = 50  # Minimum daily carbs

    # Clinical minimum calorie floors (industry standard: MyFitnessPal, Noom, Lose It)
    # Going below these requires medical supervision
    MIN_CALORIES_FEMALE = 1200
    MIN_CALORIES_MALE = 1500

    # Validation ranges
    MIN_AGE = 12
    MAX_AGE = 120
    MIN_WEIGHT_KG = 30
    MAX_WEIGHT_KG = 300
    MIN_HEIGHT_CM = 100
    MAX_HEIGHT_CM = 250
    MIN_BODY_FAT_PCT = 3
    MAX_BODY_FAT_PCT = 60


class WeeklyBudgetConstants:
    """Constants for weekly macro budget feature."""

    # BMR floor: daily target never drops below this ratio of standard daily
    BMR_FLOOR_RATIO = 0.85  # Raised from 0.80 for safer minimum

    # Deficit cap: adjusted daily never drops more than this ratio below base
    # -10% is within safe 0.5-1% BW/week range (Helms 2014)
    MAX_DAILY_DEFICIT_RATIO = 0.10

    # Surplus cap: under-eating should not create extreme catch-up targets.
    # Leftover weekly budget can remain unused instead of encouraging overeating.
    MAX_DAILY_SURPLUS_RATIO = 0.25

    # Adjusted daily macro bounds (ratio of standard daily)
    MACRO_FLOOR_RATIO = 0.5  # Never below 50% of base
    MACRO_CEILING_RATIO = 1.5  # Never above 150% of base

    # Smart prompt threshold: suggest cheat tag when daily consumed > this × daily target
    SMART_PROMPT_THRESHOLD = 1.20

    # Tomorrow preview: show when deviation from base exceeds this ratio
    # 3% catches under-eating early in the week (5% missed mild under-eating
    # because deficit is spread across many remaining days)
    PREVIEW_DEVIATION_THRESHOLD = 0.03

    # Minimum logged days required for meaningful redistribution
    MIN_LOGGED_DAYS_FOR_REDISTRIBUTION = 3

    # Week starts on Monday
    WEEK_START_WEEKDAY = 0  # Monday
