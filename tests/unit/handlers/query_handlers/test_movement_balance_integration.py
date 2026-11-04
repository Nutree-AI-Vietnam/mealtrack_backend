from src.app.handlers.query_handlers.get_nutrition_bulk_query_handler import (
    GetNutritionBulkQueryHandler,
)


def test_bulk_date_summary_uses_net_calories_after_movement():
    handler = GetNutritionBulkQueryHandler()

    result = handler._build_date_summary(
        meals=[],
        target_calories=2000,
        target_macros={"protein": 100, "carbs": 200, "fat": 70},
        movement_kcal=300.0,
    )

    assert result["totals"]["consumed"]["calories"] == -300.0
    assert result["totals"]["remaining"]["calories"] == 2300.0
    assert result["movement_kcal_burned"] == 300.0


def test_bulk_date_summary_counts_food_only_when_activity_calories_off():
    handler = GetNutritionBulkQueryHandler()

    result = handler._build_date_summary(
        meals=[],
        target_calories=2000,
        target_macros={"protein": 100, "carbs": 200, "fat": 70},
        movement_kcal=300.0,
        activity_calories=False,
    )

    assert result["totals"]["consumed"]["calories"] == 0.0
    assert result["totals"]["remaining"]["calories"] == 2000.0
    assert result["movement_kcal_burned"] == 300.0
