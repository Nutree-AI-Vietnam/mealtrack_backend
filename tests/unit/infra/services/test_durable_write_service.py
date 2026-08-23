"""Unit tests for durable write fingerprint and key normalization."""

import pytest

from src.api.routes.v1.manual_meal_durable import manual_meal_fingerprint
from src.api.schemas.request.meal_requests import (
    CreateManualMealFromFoodsRequest,
    ManualMealItemRequest,
)
from src.infra.services.durable_write_service import (
    canonicalize_fingerprint,
    normalize_idempotency_key,
)


def test_canonicalize_fingerprint_is_order_independent():
    left = canonicalize_fingerprint({"b": 1, "a": {"z": 2, "y": 3}})
    right = canonicalize_fingerprint({"a": {"y": 3, "z": 2}, "b": 1})
    assert left == right


def test_canonicalize_fingerprint_changes_with_payload():
    assert canonicalize_fingerprint({"a": 1}) != canonicalize_fingerprint({"a": 2})


def test_normalize_idempotency_key_trims_and_rejects_blank():
    assert normalize_idempotency_key("  key-1  ") == "key-1"
    assert normalize_idempotency_key("   ") is None
    assert normalize_idempotency_key(None) is None


def test_normalize_idempotency_key_rejects_too_long():
    with pytest.raises(ValueError, match="160"):
        normalize_idempotency_key("k" * 161)


def test_manual_meal_fingerprint_is_stable_for_same_food_payload():
    base_item = dict(fdc_id=123, name="Oats", quantity=40, unit="g")
    left = CreateManualMealFromFoodsRequest(
        dish_name="Oats",
        meal_type="breakfast",
        target_date="2026-08-11",
        source="manual",
        items=[ManualMealItemRequest(**base_item)],
    )
    right = CreateManualMealFromFoodsRequest(
        dish_name="Oats",
        meal_type="breakfast",
        target_date="2026-08-11",
        source="manual",
        items=[ManualMealItemRequest(**base_item)],
    )
    assert manual_meal_fingerprint(left) == manual_meal_fingerprint(right)
