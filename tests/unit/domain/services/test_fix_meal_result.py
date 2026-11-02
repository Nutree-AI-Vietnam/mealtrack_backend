"""Unit tests for fix-result merge and calorie guardrails."""

import pytest

from src.domain.model.nutrition import FoodItem, Macros
from src.domain.services.meal_calorie_service import (
    assert_fix_result_calorie_guardrail,
    merge_fix_result_items,
    serialize_items_for_fix_prompt,
)


def _item(
    *,
    item_id: str,
    name: str,
    quantity: float,
    protein: float,
    carbs: float,
    fat: float,
    confidence: float = 0.9,
) -> FoodItem:
    return FoodItem(
        id=item_id,
        name=name,
        quantity=quantity,
        unit="g",
        macros=Macros(protein=protein, carbs=carbs, fat=fat),
        confidence=confidence,
    )


def test_merge_keeps_unmentioned_items_dropped_by_model():
    old = [
        _item(item_id="1", name="Chicken", quantity=120, protein=30, carbs=0, fat=5),
        _item(item_id="2", name="Rice", quantity=180, protein=4, carbs=40, fat=1),
    ]
    # Model only returned the corrected chicken and forgot rice.
    new = [
        _item(item_id="x", name="Fish", quantity=120, protein=28, carbs=0, fat=4),
    ]

    merged = merge_fix_result_items(old, new, "it was fish not chicken")

    names = [item.name for item in merged]
    assert "Fish" in names
    assert "Rice" in names
    assert "Chicken" not in names
    rice = next(item for item in merged if item.name == "Rice")
    assert rice.id == "2"
    assert rice.quantity == 180


def test_merge_preserves_id_when_name_matches():
    old = [
        _item(item_id="keep-me", name="Rice", quantity=200, protein=4, carbs=45, fat=1)
    ]
    new = [
        _item(item_id="new-id", name="Rice", quantity=100, protein=2, carbs=22, fat=0.5)
    ]

    merged = merge_fix_result_items(old, new, "I only ate half the rice")

    assert len(merged) == 1
    assert merged[0].id == "keep-me"
    assert merged[0].quantity == 100
    # Calories come from macros (backend formula), not a client field.
    assert merged[0].calories == pytest.approx(merged[0].macros.total_calories)


def test_calorie_guardrail_rejects_absurd_jump():
    with pytest.raises(ValueError, match="FIX_RESULT_CALORIE_JUMP"):
        assert_fix_result_calorie_guardrail(
            old_calories=400,
            new_calories=5000,
            correction="it was chicken",
        )


def test_calorie_guardrail_allows_jump_when_user_asks_for_more():
    assert_fix_result_calorie_guardrail(
        old_calories=400,
        new_calories=3200,
        correction="I ate a double portion",
    )


def test_calorie_guardrail_rejects_empty_calories():
    with pytest.raises(ValueError, match="FIX_RESULT_EMPTY_CALORIES"):
        assert_fix_result_calorie_guardrail(
            old_calories=400,
            new_calories=0,
            correction="remove the sauce",
        )


def test_serialize_items_for_prompt_includes_macros():
    items = [_item(item_id="1", name="Egg", quantity=50, protein=6, carbs=0.5, fat=5)]
    payload = serialize_items_for_fix_prompt(items)
    assert payload[0]["name"] == "Egg"
    assert payload[0]["protein"] == 6
    assert "calories" in payload[0]
