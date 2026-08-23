from sqlalchemy import inspect

from src.infra.database.models.food_reference_model import FoodReferenceModel
from src.infra.repositories.food_reference_adopt import (
    FoodReferenceAdoptRepository,
    _replace_relationship_collection,
)
from src.infra.repositories.food_reference_projection import (
    build_food_reference_serving_rows,
)


def _unverified_model() -> FoodReferenceModel:
    return FoodReferenceModel(
        name="fatsecret:banana",
        name_normalized="fatsecret:banana",
        source="fatsecret",
        source_namespace="fatsecret",
        source_food_id="banana",
        is_verified=False,
        density=1.0,
        fiber_100g=0,
        sugar_100g=0,
    )


def test_replace_serving_rows_does_not_require_loaded_collection():
    model = _unverified_model()
    rows = build_food_reference_serving_rows([{"name": "g", "grams": 1.0}])

    _replace_relationship_collection(model, "serving_size_rows", rows)

    assert "serving_size_rows" not in inspect(model).unloaded
    assert [row.name for row in model.serving_size_rows] == ["g"]


def test_apply_nutrition_writes_servings_when_collection_is_unloaded():
    model = _unverified_model()
    repo = FoodReferenceAdoptRepository(session=object())

    repo._apply_nutrition(
        model,
        "Banana",
        {"protein_100g": 1.1, "carbs_100g": 23.0, "fat_100g": 0.3},
        [{"name": "medium", "grams": 118.0}, {"name": "g", "grams": 1.0}],
    )

    assert model.name == "Banana"
    assert model.is_verified is True
    assert [row.name for row in model.serving_size_rows] == ["medium", "g"]
