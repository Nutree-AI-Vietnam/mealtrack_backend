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
    _convert_with_allowed_units,
    canonicalize_authoritative_quantity,
    canonicalize_mass_volume_unit,
    clamp_nutrition_values,
    convert_quantity_to_grams,
    fallback_custom_serving_options,
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


def test_manual_item_client_identity_is_preserved_as_food_item_id():
    service = NutritionCalculationService()

    _, food_items = service.aggregate_from_command_items(
        [
            ManualMealItem(
                name="Rice",
                quantity=100.0,
                unit="g",
                client_item_id="550e8400-e29b-41d4-a716-446655440001",
                custom_nutrition=CustomNutrition(
                    calories_per_100g=130.0,
                    protein_per_100g=2.7,
                    carbs_per_100g=28.0,
                    fat_per_100g=0.3,
                ),
            )
        ]
    )

    assert food_items[0].id == "550e8400-e29b-41d4-a716-446655440001"


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


def test_authoritative_snapshot_serving_weight_is_used_for_manual_save():
    service = NutritionCalculationService()

    nutrition, food_items = service.aggregate_from_command_items(
        [
            ManualMealItem(
                name="Rice",
                quantity=1.0,
                unit="cup",
                origin="local",
                food_reference_id=42,
                nutrition_contract_version="2",
                allowed_units=[
                    {"unit": "g", "gram_weight": 1.0},
                    {"unit": "cup", "gram_weight": 158.0},
                ],
                custom_nutrition=CustomNutrition(
                    calories_per_100g=124.7,
                    protein_per_100g=2.7,
                    carbs_per_100g=28.0,
                    fat_per_100g=0.3,
                ),
            )
        ]
    )

    assert food_items[0].macros.protein == pytest.approx(2.7 * 1.58)
    assert nutrition.macros.protein == pytest.approx(4.3)


def test_authoritative_snapshot_rejects_unknown_serving_unit():
    with pytest.raises(ValueError, match="authoritative source snapshot"):
        NutritionCalculationService().aggregate_from_command_items(
            [
                ManualMealItem(
                    name="Rice",
                    quantity=1.0,
                    unit="bowl",
                    origin="local",
                    food_reference_id=42,
                    nutrition_contract_version="2",
                    allowed_units=[{"unit": "g", "gram_weight": 1.0}],
                    custom_nutrition=CustomNutrition(
                        calories_per_100g=124.7,
                        protein_per_100g=2.7,
                        carbs_per_100g=28.0,
                        fat_per_100g=0.3,
                    ),
                )
            ]
        )


def test_authoritative_snapshot_does_not_match_free_text_description_tokens():
    with pytest.raises(ValueError, match="authoritative source snapshot"):
        scale_per_100g_nutrition(
            {"calories": 77.0},
            quantity=100,
            unit="potato",
            allowed_units=[
                {
                    "unit": "medium",
                    "gram_weight": 173.0,
                    "description": "1 medium potato",
                }
            ],
            food_name="Potato",
            strict_allowed_units=True,
        )


def test_authoritative_snapshot_does_not_strip_arbitrary_unit_suffixes(caplog):
    raw_unit = "medium private-text"
    with pytest.raises(ValueError, match="authoritative source snapshot"):
        scale_per_100g_nutrition(
            {"calories": 77.0},
            quantity=100,
            unit=raw_unit,
            allowed_units=[
                {
                    "unit": "medium",
                    "gram_weight": 173.0,
                    "description": "1 medium potato",
                }
            ],
            food_name="Potato",
            strict_allowed_units=True,
        )

    assert raw_unit not in caplog.text


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


def test_allowed_unit_logs_do_not_expose_unit_or_description(caplog):
    sensitive_unit = "private-unit"
    sensitive_description = "confidential serving private-unit"
    caplog.set_level("INFO", logger="src.domain.services.nutrition_calculation_service")

    scaled = scale_per_100g_nutrition(
        {"calories": 100.0},
        quantity=1.0,
        unit=sensitive_unit,
        allowed_units=[
            {
                "unit": "portion",
                "description": sensitive_description,
                "gram_weight": 25.0,
            }
        ],
    )

    assert scaled["calories"] == 25.0
    assert "Unit keyword matched an allowed-unit description" in caplog.text
    assert sensitive_unit not in caplog.text
    assert sensitive_description not in caplog.text

    caplog.clear()
    fallback_unit = "private-fallback-unit"
    fallback_description = "confidential fallback serving"
    scaled = scale_per_100g_nutrition(
        {"calories": 100.0},
        quantity=1.0,
        unit=fallback_unit,
        allowed_units=[
            {
                "unit": "portion",
                "description": fallback_description,
                "gram_weight": 25.0,
            }
        ],
    )

    assert scaled["calories"] == 25.0
    assert "Unknown unit used the default allowed serving" in caplog.text
    assert fallback_unit not in caplog.text
    assert fallback_description not in caplog.text

    caplog.clear()
    raw_unit = "private family serving"
    assert convert_quantity_to_grams(100, raw_unit, "Rice") == 100
    assert "Unknown unit used quantity as grams" in caplog.text
    assert raw_unit not in caplog.text


def test_herb_sprig_units_use_countable_serving_grams():
    assert convert_quantity_to_grams(1, "nhánh", "Cilantro") == 100
    assert convert_quantity_to_grams(1, "sprig", "Cilantro") == 100
    assert (
        quantity_to_grams(
            1,
            "nhánh",
            "Cilantro",
            [
                {"unit": "g", "gram_weight": 1.0},
                {"unit": "nhánh", "gram_weight": 4.0},
            ],
        )
        == 4
    )


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


def test_unknown_tuber_unit_uses_countable_provider_serving_not_one_gram():
    allowed_units = [
        {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
        {
            "unit": "sweetpotato",
            "gram_weight": 130.0,
            "description": "1 sweetpotato",
        },
        {"unit": "oz", "gram_weight": 28.35, "description": "1 oz"},
        {"unit": "cup", "gram_weight": 200.0, "description": "1 cup, mashed"},
    ]

    assert _convert_with_allowed_units(
        1, "củ lớn", allowed_units, "Khoai lang"
    ) == pytest.approx(130.0)
    with pytest.raises(ValueError):
        _convert_with_allowed_units(
            1, "củ lớn", allowed_units, "Khoai lang", strict=True
        )


def test_gram_alias_is_one_gram_even_when_a_100g_row_exists():
    allowed_units = [
        {"unit": "gram", "gram_weight": 100.0, "description": "100 gram"},
        {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
        {"unit": "serving", "gram_weight": 100.0, "description": "1 serving"},
        {"unit": "cup", "gram_weight": 120.0, "description": "1 cup"},
    ]

    assert _convert_with_allowed_units(100, "gram", allowed_units, "Beef") == 100
    assert _convert_with_allowed_units(100, "grams", allowed_units, "Beef") == 100
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


def test_fallback_custom_serving_options_keep_selected_countable_unit():
    options = fallback_custom_serving_options("Miếng", "Sườn Nướng")
    units = {option["unit"]: option["gram_weight"] for option in options}

    assert units["g"] == pytest.approx(1.0)
    assert units["miếng"] == pytest.approx(100.0)
    quantity, unit, used_fallback = canonicalize_authoritative_quantity(
        1, "Miếng", options, "Sườn Nướng"
    )
    assert quantity == pytest.approx(1.0)
    assert unit == "Miếng"
    assert used_fallback is False
