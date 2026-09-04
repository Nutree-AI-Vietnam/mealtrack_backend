"""Unit tests for meal content fingerprinting and deduplication."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.domain.model.meal import Meal, MealStatus
from src.domain.model.nutrition import FoodItem, Macros, Nutrition, NutritionOverride
from src.domain.services.meal_fingerprint_service import (
    compute_meal_content_fingerprint,
    deduplicate_recent_meals,
)


def _make_sample_meal(
    dish_name: str,
    items: list[tuple[str, float, str, float, float, float]],
    meal_id: str | None = None,
    user_id: str | None = None,
    created_at: datetime | None = None,
    nutrition_override: NutritionOverride | None = None,
) -> Meal:
    food_items = [
        FoodItem(
            id=str(uuid4()),
            name=name,
            quantity=qty,
            unit=unit,
            macros=Macros(protein=p, carbs=c, fat=f),
        )
        for name, qty, unit, p, c, f in items
    ]
    tot_p = sum(p for _, _, _, p, _, _ in items)
    tot_c = sum(c for _, _, _, _, c, _ in items)
    tot_f = sum(f for _, _, _, _, _, f in items)

    now = created_at or datetime.now(UTC)
    return Meal(
        meal_id=meal_id or str(uuid4()),
        user_id=user_id or str(uuid4()),
        status=MealStatus.READY,
        dish_name=dish_name,
        created_at=now,
        ready_at=now,
        image=None,
        nutrition=Nutrition(
            macros=Macros(protein=tot_p, carbs=tot_c, fat=tot_f),
            food_items=food_items,
            nutrition_override=nutrition_override,
        ),
    )


@pytest.mark.unit
def test_same_content_produces_identical_fingerprint_despite_ids_and_dates():
    meal1 = _make_sample_meal(
        "Chicken Salad",
        [
            ("Chicken Breast", 150.0, "g", 46.5, 0.0, 5.4),
            ("Lettuce", 50.0, "g", 0.7, 1.5, 0.1),
        ],
        meal_id=str(uuid4()),
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
    )
    meal2 = _make_sample_meal(
        "chicken salad",  # Case insensitive dish name
        [
            ("lettuce", 50.0, "g", 0.7, 1.5, 0.1),
            ("Chicken Breast", 150.0, "g", 46.5, 0.0, 5.4),
        ],  # Order independent
        meal_id=str(uuid4()),
        created_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
    )
    fp1 = compute_meal_content_fingerprint(meal1)
    fp2 = compute_meal_content_fingerprint(meal2)
    assert fp1 == fp2


@pytest.mark.unit
def test_different_portion_produces_different_fingerprint():
    meal1 = _make_sample_meal(
        "Chicken Rice",
        [
            ("Chicken Breast", 150.0, "g", 46.5, 0.0, 5.4),
            ("White Rice", 200.0, "g", 5.0, 56.0, 0.6),
        ],
    )
    meal2 = _make_sample_meal(
        "Chicken Rice",
        [
            ("Chicken Breast", 200.0, "g", 62.0, 0.0, 7.2),
            ("White Rice", 200.0, "g", 5.0, 56.0, 0.6),
        ],
    )
    assert compute_meal_content_fingerprint(meal1) != compute_meal_content_fingerprint(
        meal2
    )


@pytest.mark.unit
def test_same_foods_and_grams_match_despite_different_dish_names():
    """AC: same meal = same foods + grams; dish name is not part of identity."""
    items = [
        ("Chicken Breast", 150.0, "g", 46.5, 0.0, 5.4),
        ("White Rice", 200.0, "g", 5.0, 56.0, 0.6),
    ]
    meal1 = _make_sample_meal("Chicken Rice Bowl", items)
    meal2 = _make_sample_meal("Com Ga", items)
    assert compute_meal_content_fingerprint(meal1) == compute_meal_content_fingerprint(
        meal2
    )


@pytest.mark.unit
def test_itemless_meals_fall_back_to_dish_name_identity():
    meal1 = _make_sample_meal("Mystery Bowl", [])
    meal2 = _make_sample_meal("Other Bowl", [])
    meal3 = _make_sample_meal("mystery bowl", [])
    assert compute_meal_content_fingerprint(meal1) != compute_meal_content_fingerprint(
        meal2
    )
    assert compute_meal_content_fingerprint(meal1) == compute_meal_content_fingerprint(
        meal3
    )


@pytest.mark.unit
def test_deduplicate_recent_meals_preserves_newest_and_limits():
    m1_id = str(uuid4())
    m2_id = str(uuid4())
    m3_id = str(uuid4())
    m4_id = str(uuid4())

    m1 = _make_sample_meal(
        "Chicken Salad",
        [("Chicken Breast", 150.0, "g", 46.5, 0.0, 5.4)],
        meal_id=m1_id,
        created_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
    )
    m2 = _make_sample_meal(
        "Beef Steak",
        [("Beef Steak", 200.0, "g", 50.0, 0.0, 20.0)],
        meal_id=m2_id,
        created_at=datetime(2026, 8, 19, 12, 0, tzinfo=UTC),
    )
    m3 = _make_sample_meal(
        "Chicken Salad",
        [("Chicken Breast", 150.0, "g", 46.5, 0.0, 5.4)],
        meal_id=m3_id,
        created_at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
    )
    m4 = _make_sample_meal(
        "Eggs Toast",
        [("Eggs", 100.0, "g", 12.0, 1.0, 10.0)],
        meal_id=m4_id,
        created_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    )

    deduped = deduplicate_recent_meals([m1, m2, m3, m4], limit=2)
    assert len(deduped) == 2
    assert deduped[0].meal_id == m1_id
    assert deduped[1].meal_id == m2_id
