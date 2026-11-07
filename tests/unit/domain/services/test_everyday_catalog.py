from datetime import UTC, datetime

import pytest

from src.domain.services.everyday_catalog import (
    EverydaySlot,
    build_import_manifest,
    clamp_batch_size,
    draft_from_payload,
    everyday_recipe_key,
    local_day,
    plan_slots,
)
from src.domain.services.meal_recommendation.catalog_recipe_seed_validator import (
    validate_catalog_seed_manifest,
)


def _slot() -> EverydaySlot:
    return plan_slots(datetime(2026, 10, 29, tzinfo=UTC).date(), 5, market="US")[0]


def _payload(**overrides):
    body = {
        "name": "Scrambled eggs",
        "ingredients": [
            {"name": "egg", "amount": 2, "unit": "g"},
            {"name": "butter", "amount": 5, "unit": "g"},
            {"name": "salt", "amount": 1, "unit": "g"},
        ],
        "recipe_steps": [
            {"step": 1, "instruction": "Beat the eggs.", "duration_minutes": 1},
            {"step": 2, "instruction": "Cook them gently.", "duration_minutes": 4},
        ],
        "prep_time_minutes": 10,
    }
    body.update(overrides)
    return body


def test_batch_stays_between_five_and_ten():
    assert clamp_batch_size(3) == 5
    assert clamp_batch_size(8) == 8
    assert clamp_batch_size(40) == 10


def test_saigon_day_rolls_at_local_midnight():
    # 17:30 UTC is 00:30 the next day in Saigon.
    assert local_day(datetime(2026, 10, 28, 17, 30, tzinfo=UTC)).isoformat() == (
        "2026-10-29"
    )


def test_plan_alternates_countries_and_rotates_families():
    day = datetime(2026, 10, 29, tzinfo=UTC).date()
    slots = plan_slots(day, 8)
    assert [slot.market for slot in slots] == ["VN", "US"] * 4
    assert {slot.meal_type for slot in slots} == {"breakfast", "lunch", "dinner"}
    assert (
        plan_slots(day, 8)[0].dish_family
        != plan_slots(day.replace(day=30), 8)[0].dish_family
    )


def test_vietnamese_name_becomes_a_stable_key():
    assert everyday_recipe_key("vietnamese", "Phở bò") == "everyday-vietnamese-pho-bo"


def test_draft_rejects_cups_and_keeps_grams():
    assert draft_from_payload(_slot(), _payload()) is not None
    assert (
        draft_from_payload(
            _slot(),
            _payload(
                ingredients=[
                    {"name": "egg", "amount": 2, "unit": "cup"},
                    {"name": "butter", "amount": 5, "unit": "g"},
                    {"name": "salt", "amount": 1, "unit": "g"},
                ]
            ),
        )
        is None
    )


def test_manifest_passes_partial_catalog_validation():
    draft = draft_from_payload(_slot(), _payload(name="Phở bò"))
    assert draft is not None
    manifest = build_import_manifest(draft)
    result = validate_catalog_seed_manifest(
        manifest,
        expected_recipe_count=1,
        min_per_cuisine_meal_type=0,
        expected_cuisine_counts=None,
        allow_declared_expected_count_mismatch=True,
        allowed_cuisines=None,
        required_cuisines=(),
    )
    assert result.is_valid
    recipe = manifest["recipes"][0]
    assert recipe["cuisine"] == "american"
    assert recipe["source_name"] == "everyday-catalog"
    assert "calories" not in recipe


def test_naive_moment_is_rejected():
    with pytest.raises(ValueError):
        local_day(datetime(2026, 10, 29, 1, 0))
