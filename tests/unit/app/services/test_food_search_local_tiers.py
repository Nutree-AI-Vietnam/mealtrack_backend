"""Local catalog coverage and strong/weak ordering for food search."""

import pytest

from src.app.services.food_search_local_tiers import (
    local_results_are_sufficient,
    merge_local_tiers,
    split_strong_matches,
)


def _row(row_id, name, name_vi=None):
    return {"id": row_id, "name": name, "description": name, "name_vi": name_vi}


def _ids(rows):
    return [row["id"] for row in rows]


def _strong_rice(count):
    return [_row(index, f"Rice dish {index}") for index in range(count)]


def _weak_rice(count):
    # "rice" only appears inside a longer word.
    return [_row(100 + index, f"Licorice {index}") for index in range(count)]


def test_split_keeps_order_within_each_tier():
    items = [_row(1, "Licorice"), _row(2, "Fried rice"), _row(3, "Rice noodles")]

    strong, weak = split_strong_matches(items, "rice")

    assert _ids(strong) == [2, 3]
    assert _ids(weak) == [1]


def test_row_is_strong_when_any_query_matches_any_of_its_names():
    items = [
        _row(1, "Hainanese chicken rice", "Cơm gà Hải Nam"),
        _row(2, "Chicken rice"),
        _row(3, "Chicken soup"),
    ]

    strong, weak = split_strong_matches(items, None, "com ga", "chicken rice")

    assert _ids(strong) == [1, 2]
    assert _ids(weak) == [3]


@pytest.mark.parametrize(
    ("items", "limit", "expected"),
    [
        ([], 20, False),
        (_weak_rice(3), 3, True),
        (_strong_rice(5) + _weak_rice(2), 20, True),
        (_strong_rice(4) + _weak_rice(6), 20, False),
        (_strong_rice(2), 3, False),
    ],
    ids=[
        "empty",
        "full_page_of_weak_rows",
        "short_page_with_a_screen_of_strong_rows",
        "short_page_with_too_few_strong_rows",
        "short_page_below_a_small_limit",
    ],
)
def test_local_rows_are_sufficient_for_a_full_page_or_a_screen_of_strong_rows(
    items, limit, expected
):
    assert local_results_are_sufficient(items, limit, "rice") is expected


def test_merge_orders_strong_before_weak_and_native_before_translated():
    native = [
        _row(1, "Fried chicken", "Gà rán"),
        _row(2, "Hainanese chicken rice", "Cơm gà Hải Nam"),
    ]
    translated = [
        _row(3, "Licorice chicken"),
        _row(2, "Hainanese chicken rice", "Cơm gà Hải Nam"),
        _row(4, "Chicken rice"),
    ]

    merged = merge_local_tiers(
        native,
        translated,
        limit=10,
        key=lambda row: row["id"],
        queries=["cơm gà", "chicken rice"],
    )

    assert _ids(merged) == [2, 4, 1, 3]


def test_merge_stops_at_the_limit():
    merged = merge_local_tiers(
        _weak_rice(2),
        _strong_rice(3),
        limit=4,
        key=lambda row: row["id"],
        queries=["rice"],
    )

    assert _ids(merged) == [0, 1, 2, 100]
