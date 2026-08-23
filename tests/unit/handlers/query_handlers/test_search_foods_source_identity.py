from src.app.handlers.query_handlers.search_foods_query_handler import (
    SearchFoodsQueryHandler,
)


def test_merge_keeps_distinct_provider_ids_with_same_display_name():
    handler = SearchFoodsQueryHandler(
        cache_service=None,
        mapping_service=None,
        fat_secret_service=None,
    )

    result = handler._merge_search_results(
        [],
        [
            {
                "source": "fatsecret",
                "source_namespace": "fatsecret",
                "source_food_id": "1",
                "description": "Rice",
            },
            {
                "source": "fatsecret",
                "source_namespace": "fatsecret",
                "source_food_id": "2",
                "description": "Rice",
            },
        ],
        10,
    )

    assert [item["source_food_id"] for item in result] == ["1", "2"]


def test_merge_keeps_local_food_references_beside_fatsecret():
    handler = SearchFoodsQueryHandler(
        cache_service=None,
        mapping_service=None,
        fat_secret_service=None,
    )

    result = handler._merge_search_results(
        [
            {
                "source": "food_reference",
                "food_reference_id": 42,
                "origin": "local",
                "source_namespace": "food_reference",
                "source_food_id": "42",
                "description": "Rice noodles",
                "name_normalized": "rice noodles",
            }
        ],
        [
            {
                "source": "fatsecret",
                "source_namespace": "fatsecret",
                "source_food_id": "fs-beef",
                "food_id": "fs-beef",
                "description": "Beef",
            }
        ],
        10,
    )

    assert [
        (item.get("origin") or item.get("source"), item.get("source_food_id"))
        for item in result
    ] == [
        ("local", "42"),
        ("fatsecret", "fs-beef"),
    ]
