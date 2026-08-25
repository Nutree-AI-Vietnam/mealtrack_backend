import pytest
from pydantic import ValidationError

from src.api.schemas.request.meal_requests import (
    CreateManualMealFromFoodsRequest,
    EditMealIngredientsRequest,
    NutritionOverrideRequest,
)


def _v2_create_item(**overrides):
    item = {
        "origin": "local",
        "food_reference_id": 42,
        "quantity": 150,
        "unit": "g",
    }
    item.update(overrides)
    return item


def test_v2_reference_item_requires_one_matching_source_identity():
    payload = CreateManualMealFromFoodsRequest(
        dish_name="Rice",
        nutrition_contract_version=2,
        items=[_v2_create_item()],
    )

    assert payload.items[0].origin == "local"


def test_v2_create_accepts_arbitrary_unit_for_backend_normalization():
    payload = CreateManualMealFromFoodsRequest(
        dish_name="Rice",
        nutrition_contract_version=2,
        items=[_v2_create_item(quantity=100, unit="my family bowl")],
    )

    assert payload.items[0].unit == "my family bowl"


def test_v2_custom_item_is_source_less_and_keeps_custom_nutrition():
    payload = CreateManualMealFromFoodsRequest(
        dish_name="Homemade rice",
        nutrition_contract_version=2,
        items=[
            {
                "origin": "custom",
                "name": "Homemade rice",
                "quantity": 100,
                "unit": "g",
                "custom_nutrition": {
                    "protein_per_100g": 2.7,
                    "carbs_per_100g": 28,
                    "fat_per_100g": 0.3,
                },
            }
        ],
    )

    assert payload.items[0].origin == "custom"


def test_v2_prepared_source_item_requires_and_keeps_snapshot():
    nutrition = {
        "protein_per_100g": 2.7,
        "carbs_per_100g": 28,
        "fat_per_100g": 0.3,
    }
    snapshot = {
        "basis": "100g",
        "protein_per_100g": 2.7,
        "carbs_per_100g": 28,
        "fat_per_100g": 0.3,
    }

    payload = CreateManualMealFromFoodsRequest(
        dish_name="Rice",
        nutrition_contract_version=2,
        items=[
            _v2_create_item(
                custom_nutrition=nutrition,
                source_snapshot=snapshot,
                allowed_units=[{"unit": "g", "gram_weight": 1, "description": "1 g"}],
            )
        ],
    )

    assert payload.items[0].source_snapshot == snapshot

    with pytest.raises(ValidationError, match="source_snapshot"):
        CreateManualMealFromFoodsRequest(
            dish_name="Rice",
            nutrition_contract_version=2,
            items=[_v2_create_item(custom_nutrition=nutrition)],
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "dish_name": "Rice",
            "items": [_v2_create_item()],
        },
        {
            "dish_name": "Rice",
            "nutrition_contract_version": 2,
            "items": [_v2_create_item(origin="local", fdc_id=170000)],
        },
        {
            "dish_name": "Rice",
            "nutrition_contract_version": 2,
            "items": [
                {
                    "origin": "custom",
                    "name": "Rice",
                    "quantity": 100,
                    "unit": "g",
                }
            ],
        },
    ],
)
def test_v2_create_rejects_missing_version_mismatched_ids_and_missing_custom_data(
    payload,
):
    with pytest.raises(ValidationError):
        CreateManualMealFromFoodsRequest.model_validate(payload)


def test_v2_remove_accepts_extra_edit_fields():
    request = EditMealIngredientsRequest(
        nutrition_contract_version=2,
        food_item_changes=[
            {
                "action": "remove",
                "id": "item-1",
                "name": "Ignored name",
                "quantity": 100,
                "unit": "g",
                "nutrition_override": {
                    "calories": 500,
                    "protein": 20,
                    "carbs": 30,
                    "fat": 15,
                },
                "clear_nutrition_override": True,
                "food_reference_id": 42,
            }
        ],
    )

    assert request.food_item_changes[0].id == "item-1"


@pytest.mark.parametrize("nutrition_contract_version", [None, 2])
def test_remove_override_is_accepted_for_legacy_and_v2_clients(
    nutrition_contract_version,
):
    request = EditMealIngredientsRequest(
        nutrition_contract_version=nutrition_contract_version,
        food_item_changes=[
            {
                "action": "remove",
                "id": "item-1",
                "nutrition_override": {
                    "calories": 500,
                    "protein": 20,
                    "carbs": 30,
                    "fat": 15,
                },
            }
        ],
    )

    assert request.food_item_changes[0].id == "item-1"


def test_v2_quantity_update_strips_legacy_source_echoes():
    payload = EditMealIngredientsRequest(
        nutrition_contract_version=2,
        food_item_changes=[
            {
                "action": "update",
                "id": "item-1",
                "name": "Echoed source name",
                "quantity": 200,
                "unit": "g",
                "custom_nutrition": {
                    "protein_per_100g": 10,
                    "carbs_per_100g": 20,
                    "fat_per_100g": 8,
                },
                "allowed_units": [
                    {"unit": "g", "gram_weight": 1, "description": "1 g"}
                ],
            }
        ],
    )

    change = payload.food_item_changes[0]
    assert change.id == "item-1"
    assert change.quantity == 200
    assert change.unit == "g"
    assert change.name is None
    assert change.custom_nutrition is None
    assert change.allowed_units == []


def test_v2_quantity_update_rejects_identity_with_legacy_source_echoes():
    with pytest.raises(ValidationError, match="cannot replace source"):
        EditMealIngredientsRequest(
            nutrition_contract_version=2,
            food_item_changes=[
                {
                    "action": "update",
                    "id": "item-1",
                    "fdc_id": 999999,
                    "name": "Echoed source name",
                    "quantity": 200,
                    "unit": "g",
                    "custom_nutrition": {
                        "protein_per_100g": 10,
                        "carbs_per_100g": 20,
                        "fat_per_100g": 8,
                    },
                }
            ],
        )


def test_v2_source_replacement_without_portion_fields_is_still_rejected():
    with pytest.raises(ValidationError, match="cannot replace source"):
        EditMealIngredientsRequest(
            nutrition_contract_version=2,
            food_item_changes=[
                {
                    "action": "update",
                    "id": "item-1",
                    "name": "Replacement source",
                    "custom_nutrition": {
                        "protein_per_100g": 10,
                        "carbs_per_100g": 20,
                        "fat_per_100g": 8,
                    },
                }
            ],
        )


def test_v2_item_override_defaults_user_intent():
    request = EditMealIngredientsRequest(
        nutrition_contract_version=2,
        food_item_changes=[
            {
                "action": "update",
                "id": "item-1",
                "nutrition_override": {
                    "calories": 500,
                    "protein": 20,
                    "carbs": 30,
                    "fat": 15,
                },
            }
        ],
    )

    assert request.food_item_changes[0].override_intent == "user_entered"


def test_v2_clear_item_override_defaults_user_intent():
    request = EditMealIngredientsRequest(
        nutrition_contract_version=2,
        food_item_changes=[
            {
                "action": "update",
                "id": "item-1",
                "clear_nutrition_override": True,
            }
        ],
    )

    assert request.food_item_changes[0].override_intent == "user_entered"


def test_v2_clear_item_override_accepts_quantity_edit():
    request = EditMealIngredientsRequest(
        nutrition_contract_version=2,
        food_item_changes=[
            {
                "action": "update",
                "id": "item-1",
                "quantity": 200,
                "unit": "g",
                "clear_nutrition_override": True,
            }
        ],
    )

    change = request.food_item_changes[0]
    assert change.quantity == 200
    assert change.override_intent == "user_entered"


@pytest.mark.parametrize(
    "override_fields",
    [
        {
            "nutrition_override": {
                "calories": 500,
                "protein": 20,
                "carbs": 30,
                "fat": 15,
            }
        },
        {"clear_nutrition_override": True},
    ],
)
def test_legacy_add_accepts_item_override_actions(override_fields):
    request = EditMealIngredientsRequest(
        food_item_changes=[
            {
                "action": "add",
                "id": "client-generated-id",
                "name": "Rau xao",
                "quantity": 100,
                "unit": "g",
                **override_fields,
            }
        ]
    )

    assert request.food_item_changes[0].id == "client-generated-id"


@pytest.mark.parametrize(
    "override_fields",
    [
        {
            "nutrition_override": {
                "calories": 500,
                "protein": 20,
                "carbs": 30,
                "fat": 15,
            }
        },
        {"clear_nutrition_override": True},
    ],
)
def test_v2_add_accepts_item_override_actions(override_fields):
    request = EditMealIngredientsRequest(
        nutrition_contract_version=2,
        food_item_changes=[
            {
                "action": "add",
                "id": "22222222-2222-4222-8222-222222222222",
                "origin": "custom",
                "name": "Rau xao",
                "quantity": 100,
                "unit": "g",
                "custom_nutrition": {
                    "protein_per_100g": 2,
                    "carbs_per_100g": 8,
                    "fat_per_100g": 1,
                },
                **override_fields,
            }
        ],
    )

    change = request.food_item_changes[0]
    assert change.id == "22222222-2222-4222-8222-222222222222"
    assert change.override_intent == "user_entered"


def test_v2_meal_override_without_intent_is_compatible_with_legacy_clients():
    request = EditMealIngredientsRequest(
        nutrition_contract_version=2,
        nutrition_override={
            "calories": 500,
            "protein": 20,
            "carbs": 30,
            "fat": 15,
        },
    )

    assert request.override_intent == "user_entered"


def test_nutrition_override_rejects_negative_values():
    with pytest.raises(ValidationError):
        NutritionOverrideRequest(
            calories=500,
            protein=-1,
            carbs=3000,
            fat=1500,
        )


def test_nutrition_override_accepts_large_nonnegative_values():
    request = NutritionOverrideRequest(
        calories=100000,
        protein=5000,
        carbs=3000,
        fat=1500,
    )

    assert request.calories == 100000
    assert request.protein == 5000
    assert request.carbs == 3000
    assert request.fat == 1500


def test_nutrition_override_accepts_absolute_calories_above_density_bound():
    request = EditMealIngredientsRequest(
        nutrition_contract_version=2,
        override_intent="user_entered",
        nutrition_override=NutritionOverrideRequest(
            calories=1500,
            protein=80,
            carbs=120,
            fat=70,
        ),
    )

    assert request.nutrition_override.calories == 1500


def test_nutrition_override_rejects_non_finite_calories():
    with pytest.raises(ValidationError):
        NutritionOverrideRequest(
            calories=float("inf"),
            protein=20,
            carbs=30,
            fat=15,
        )


def test_unknown_nutrition_contract_version_is_rejected():
    with pytest.raises(ValidationError):
        CreateManualMealFromFoodsRequest(
            dish_name="Rice",
            nutrition_contract_version=3,
            items=[{"name": "Rice", "quantity": 100, "unit": "g"}],
        )
