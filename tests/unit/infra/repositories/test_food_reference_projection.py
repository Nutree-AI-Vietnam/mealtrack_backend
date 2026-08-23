from unittest.mock import MagicMock

from src.infra.database.models.food_reference_nutrient import (
    FoodReferenceNutrientModel,
)
from src.infra.database.models.food_reference_serving_size import (
    FoodReferenceServingSizeModel,
)
from src.infra.repositories.food_reference_projection import (
    build_food_reference_serving_rows,
    food_reference_model_to_dict,
    food_reference_model_to_nutrition_projection,
)


def _make_food_reference_model(name_normalized: str) -> MagicMock:
    """Build a minimal FoodReferenceModel-like mock."""
    model = MagicMock()
    model.name_normalized = name_normalized
    model.id = 1
    model.barcode = None
    model.name = "chicken breast"
    model.name_vi = None
    model.brand = None
    model.category = None
    model.region = "global"
    model.fdc_id = None
    model.protein_100g = 23.0
    model.carbs_100g = 0.0
    model.fat_100g = 2.5
    model.fiber_100g = 0.0
    model.sugar_100g = 0.0
    model.serving_size_rows = []
    model.serving_sizes = None
    model.density = 1.0
    model.serving_size = None
    model.nutrient_rows = []
    model.extra_nutrients = None
    model.source = "fatsecret"
    model.is_verified = False
    model.image_url = None
    return model


def test_food_reference_nutrient_projection_preserves_legacy_scalar_shape():
    model = _make_food_reference_model("spinach")
    model.extra_nutrients = {"calcium_mg": 99}
    model.nutrient_rows = [
        FoodReferenceNutrientModel(
            nutrient_key="calcium_mg",
            amount=120.0,
            unit="mg",
        )
    ]

    result = food_reference_model_to_dict(model)

    assert result["extra_nutrients"]["calcium_mg"] == 120.0


def test_food_reference_nutrient_projection_preserves_legacy_object_shape():
    model = _make_food_reference_model("spinach")
    model.extra_nutrients = {
        "calcium_mg": {"amount": 99, "unit": "mg", "source": "old"}
    }
    model.nutrient_rows = [
        FoodReferenceNutrientModel(
            nutrient_key="calcium_mg",
            amount=120.0,
            unit="mg",
        )
    ]

    result = food_reference_model_to_dict(model)

    assert result["extra_nutrients"]["calcium_mg"] == {
        "amount": 120.0,
        "unit": "mg",
        "source": "old",
    }


def test_food_reference_projection_keeps_exact_serving_description_and_label():
    model = _make_food_reference_model("rice")
    model.serving_size_rows = build_food_reference_serving_rows(
        [
            {
                "unit": "cup, cooked, diced",
                "gram_weight": 158,
                "description": "1 cup cooked, diced",
                "display_description": "cốc, đã nấu, thái hạt lựu",
            }
        ]
    )

    result = food_reference_model_to_dict(model)
    cooked = next(
        option
        for option in result["allowed_units"]
        if option["unit"] == "cup, cooked, diced"
    )
    assert cooked["description"] == "1 cup cooked, diced"
    assert cooked["display_description"] == "cốc, đã nấu, thái hạt lựu"


def test_food_reference_projection_returns_allowed_units_from_serving_rows():
    model = _make_food_reference_model("spinach")
    model.serving_size_rows = build_food_reference_serving_rows(
        [{"unit": "serving", "gram_weight": 85}]
    )

    result = food_reference_model_to_dict(model)

    assert result["allowed_units"] == [
        {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
        {"unit": "serving", "gram_weight": 85.0, "description": "serving"},
    ]


def test_food_reference_nutrition_projection_uses_normalized_serving_rows():
    model = _make_food_reference_model("soy_sauce")
    model.id = 44
    model.name = "Soy sauce"
    model.source = "catalog_seed"
    model.is_verified = True
    model.density = 1.2
    model.serving_size_rows = [
        FoodReferenceServingSizeModel(
            name="tbsp",
            grams=None,
            milliliters=15,
            is_default=True,
        )
    ]

    result = food_reference_model_to_nutrition_projection(model)

    assert result.id == 44
    assert result.source == "catalog_seed"
    assert result.is_verified is True
    assert result.density_g_ml == 1.2
    assert result.servings[0].name == "tbsp"
    assert result.servings[0].milliliters == 15


def test_food_reference_nutrition_projection_supports_legacy_serving_json():
    model = _make_food_reference_model("rice")
    model.serving_size_rows = []
    model.serving_sizes = [{"unit": "bowl", "gram_weight": 180}]

    result = food_reference_model_to_nutrition_projection(model)

    assert result.servings[0].name == "bowl"
    assert result.servings[0].grams == 180
