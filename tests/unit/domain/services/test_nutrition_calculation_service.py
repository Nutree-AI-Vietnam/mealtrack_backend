from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.app.commands.meal.create_manual_meal_command import (
    CustomNutrition,
    ManualMealItem,
)
from src.domain.model.meal import Meal, MealImage, MealStatus
from src.domain.model.meal.food_item_change import (
    CustomNutritionData,
    FoodItemChange,
)
from src.domain.model.nutrition import Macros, Nutrition
from src.domain.services.meal_service import MealService
from src.domain.services.nutrition_calculation_service import (
    NutritionCalculationService,
    _convert_with_serving_options,
    canonicalize_mass_volume_unit,
    clamp_nutrition_values,
    convert_quantity_to_grams,
    normalize_unit_for_manual_save,
    quantity_to_grams,
    reconcile_calories_per_100g,
    scale_per_100g_nutrition,
)


def test_manual_custom_nutrition_uses_unit_grams_for_large_eggs():
    service = NutritionCalculationService()

    nutrition, food_items = service.aggregate_from_command_items(
        [
            ManualMealItem(
                name="Eggs",
                quantity=2.0,
                unit="large",
                custom_nutrition=CustomNutrition(
                    calories_per_100g=143.0,
                    protein_per_100g=12.6,
                    carbs_per_100g=0.7,
                    fat_per_100g=9.5,
                ),
            )
        ]
    )

    assert nutrition.macros.protein == pytest.approx(12.6)
    assert nutrition.macros.carbs == pytest.approx(0.7)
    assert nutrition.macros.fat == pytest.approx(9.5)
    assert food_items[0].calories == pytest.approx(138.7)


def test_manual_custom_nutrition_uses_density_for_oil_ml():
    service = NutritionCalculationService()

    nutrition, food_items = service.aggregate_from_command_items(
        [
            ManualMealItem(
                name="Cooking oil",
                quantity=5.0,
                unit="ml",
                custom_nutrition=CustomNutrition(
                    calories_per_100g=828.0,
                    protein_per_100g=0.0,
                    carbs_per_100g=0.0,
                    fat_per_100g=92.0,
                ),
            )
        ]
    )

    assert nutrition.macros.protein == pytest.approx(0.0)
    assert nutrition.macros.carbs == pytest.approx(0.0)
    assert nutrition.macros.fat == pytest.approx(4.2)
    assert food_items[0].macros.fat == pytest.approx(4.232)


def test_removed_source_serving_metadata_uses_global_fallback():
    nutrition, food_items = NutritionCalculationService().aggregate_from_command_items(
        [
            ManualMealItem(
                name="Rice",
                quantity=1.0,
                unit="bowl",
                custom_nutrition=CustomNutrition(
                    calories_per_100g=124.7,
                    protein_per_100g=2.7,
                    carbs_per_100g=28.0,
                    fat_per_100g=0.3,
                ),
            )
        ]
    )

    assert food_items[0].macros.protein == pytest.approx(2.7)
    assert nutrition.macros.protein == pytest.approx(2.7)


def test_meal_service_add_custom_nutrition_uses_unit_grams():
    meal = _new_processing_meal()

    updated = MealService().apply_food_item_changes(
        meal,
        [
            FoodItemChange(
                action="add",
                name="Eggs",
                quantity=2.0,
                unit="large",
                custom_nutrition=CustomNutritionData(
                    calories_per_100g=143.0,
                    protein_per_100g=12.6,
                    carbs_per_100g=0.7,
                    fat_per_100g=9.5,
                ),
            )
        ],
    )

    assert updated.nutrition.macros.protein == pytest.approx(12.6)
    assert updated.nutrition.macros.carbs == pytest.approx(0.7)
    assert updated.nutrition.macros.fat == pytest.approx(9.5)


def test_normalize_unit_for_manual_save_keeps_convertible_units():
    assert normalize_unit_for_manual_save("grams") == "g"
    assert normalize_unit_for_manual_save("quả lớn") == "large"
    assert normalize_unit_for_manual_save("cups cooked") == "cup"


def test_normalize_unit_for_manual_save_falls_back_for_ai_free_text():
    assert normalize_unit_for_manual_save("one very full noodle bowl") == "serving"


def test_unknown_units_use_safe_global_fallback():
    scaled = scale_per_100g_nutrition(
        {"calories": 100.0},
        quantity=1.0,
        unit="private-unit",
        serving_options=[{"unit": "portion", "gram_weight": 25.0}],
    )

    assert scaled["calories"] == 25.0

    assert convert_quantity_to_grams(1, "private family serving", "Rice") == 100


def test_herb_sprig_units_use_countable_serving_grams():
    assert convert_quantity_to_grams(1, "nhánh", "Cilantro") == 100
    assert convert_quantity_to_grams(1, "sprig", "Cilantro") == 100
    assert quantity_to_grams(
        1,
        "nhánh",
        "Cilantro",
        [{"unit": "g", "gram_weight": 1.0}, {"unit": "nhánh", "gram_weight": 4.0}],
    ) == 4


def test_qualitative_garnish_units_use_countable_serving_grams():
    assert convert_quantity_to_grams(1, "ít", "Hành Lá") == 100
    assert convert_quantity_to_grams(1, "pinch", "Hành Lá") == 100
    assert quantity_to_grams(
        1,
        "ít",
        "Hành Lá",
        [
            {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
            {"unit": "ít", "gram_weight": 1.0, "description": "1 ít"},
        ],
    ) == pytest.approx(100.0)


def test_bowl_alias_matches_cup_serving_not_one_gram():
    assert quantity_to_grams(
        1,
        "bát",
        "Bánh Phở",
        [
            {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
            {"unit": "cup", "gram_weight": 240.0, "description": "1 cup"},
            {"unit": "bát", "gram_weight": 1.0, "description": "1 bát"},
        ],
    ) == pytest.approx(240.0)


def test_reconcile_calories_drops_hundredfold_energy_mismatch():
    assert reconcile_calories_per_100g(4000, 40) == 40
    assert reconcile_calories_per_100g(123.4, 165) == 123.4


def test_clamp_nutrition_uses_manual_save_unit_for_ai_free_text():
    clamped = clamp_nutrition_values(
        {
            "name": "Pho bowl",
            "quantity": 1.0,
            "unit": "one very full noodle bowl",
            "english_unit": "one very full noodle bowl",
            "calories": 560.0,
            "protein": 30.0,
            "carbs": 80.0,
            "fat": 12.0,
        }
    )

    assert clamped == {
        "calories": 560.0,
        "protein": 30.0,
        "carbs": 80.0,
        "fat": 12.0,
    }


def test_unknown_tuber_unit_uses_safe_global_fallback():
    assert _convert_with_serving_options(1, "củ lớn", [], "Khoai lang") == pytest.approx(
        100.0
    )


def test_gram_alias_is_one_gram_even_when_a_100g_row_exists():
    serving_options = [
        {"unit": "gram", "gram_weight": 100.0, "description": "100 gram"},
        {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
        {"unit": "serving", "gram_weight": 100.0, "description": "1 serving"},
        {"unit": "cup", "gram_weight": 120.0, "description": "1 cup"},
    ]

    assert _convert_with_serving_options(100, "gram", serving_options, "Beef") == 100
    assert _convert_with_serving_options(100, "grams", serving_options, "Beef") == 100
    assert convert_quantity_to_grams(100, "gram", "Beef") == 100


def test_canonicalize_mass_volume_unit_maps_gram_aliases():
    assert canonicalize_mass_volume_unit("gram") == "g"
    assert canonicalize_mass_volume_unit("GRAMS") == "g"
    assert canonicalize_mass_volume_unit("ounce") == "oz"
    assert canonicalize_mass_volume_unit("miếng") == "miếng"


def _new_processing_meal() -> Meal:
    return Meal(
        meal_id=str(uuid4()),
        user_id=str(uuid4()),
        status=MealStatus.PROCESSING,
        image=MealImage(
            image_id=str(uuid4()),
            format="jpeg",
            size_bytes=1024,
            url="https://example.com/img.jpg",
        ),
        nutrition=Nutrition(
            macros=Macros(protein=0.0, carbs=0.0, fat=0.0),
            food_items=[],
        ),
        created_at=datetime.now(UTC),
    )


def test_mieng_maps_to_piece_grams_not_slice():
    assert convert_quantity_to_grams(1, "miếng") == pytest.approx(100.0)
    assert convert_quantity_to_grams(1, "lát") == pytest.approx(30.0)


def test_unknown_custom_unit_uses_safe_global_fallback():
    assert convert_quantity_to_grams(1, "Miếng", "Sườn Nướng") == pytest.approx(100.0)
