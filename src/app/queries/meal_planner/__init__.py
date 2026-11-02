"""Weekly meal planner queries."""

from .get_current_weekly_plan_query import GetCurrentWeeklyPlanQuery
from .get_meal_plan_slot_suggestions_query import GetMealPlanSlotSuggestionsQuery
from .get_recipe_detail_query import GetRecipeDetailQuery
from .get_recipe_details_query import GetRecipeDetailsQuery
from .get_recipe_summaries_query import GetRecipeSummariesQuery
from .get_weekly_groceries_query import GetWeeklyGroceriesQuery
from .list_recipes_query import ListRecipesQuery

__all__ = [
    "GetCurrentWeeklyPlanQuery",
    "GetMealPlanSlotSuggestionsQuery",
    "ListRecipesQuery",
    "GetRecipeDetailQuery",
    "GetRecipeDetailsQuery",
    "GetRecipeSummariesQuery",
    "GetWeeklyGroceriesQuery",
]
