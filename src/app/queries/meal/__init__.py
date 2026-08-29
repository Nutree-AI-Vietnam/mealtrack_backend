"""
Meal queries.
"""

from .get_daily_breakdown_query import GetDailyBreakdownQuery
from .get_daily_macros_query import GetDailyMacrosQuery
from .get_favorite_meals_query import GetFavoriteMealsQuery
from .get_meal_by_id_query import GetMealByIdQuery
from .get_meals_by_date_query import GetMealsByDateQuery
from .get_recent_meals_query import GetRecentMealsQuery
from .get_streak_query import GetStreakQuery

__all__ = [
    "GetMealByIdQuery",
    "GetDailyMacrosQuery",
    "GetMealsByDateQuery",
    "GetStreakQuery",
    "GetDailyBreakdownQuery",
    "GetRecentMealsQuery",
    "GetFavoriteMealsQuery",
]
