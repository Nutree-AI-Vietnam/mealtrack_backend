"""
query_handlers - Individual handler files.
Each handler is in its own file for better maintainability.
"""

# Activity handlers
from .get_body_fat_visual_profile_query_handler import (
    GetBodyFatVisualProfileQueryHandler,
)
from .get_bulk_activities_query_handler import GetBulkActivitiesQueryHandler
from .get_daily_activities_query_handler import GetDailyActivitiesQueryHandler
from .get_daily_breakdown_query_handler import GetDailyBreakdownQueryHandler

# Hydration handlers
from .get_daily_hydration_query_handler import GetDailyHydrationQueryHandler
from .get_daily_macros_query_handler import GetDailyMacrosQueryHandler
from .get_daily_movement_query_handler import GetDailyMovementQueryHandler
from .get_drink_catalog_query_handler import GetDrinkCatalogQueryHandler
from .get_food_details_query_handler import GetFoodDetailsQueryHandler
from .get_provider_food_details_query_handler import (
    GetProviderFoodDetailsQueryHandler,
)
from .get_popular_staples_query_handler import GetPopularStaplesQueryHandler
from .get_journey_progress_query_handler import GetJourneyProgressQueryHandler

# Meal handlers
from .get_meal_by_id_query_handler import GetMealByIdQueryHandler
from .get_meals_by_date_query_handler import GetMealsByDateQueryHandler
from .get_movement_catalog_query_handler import GetMovementCatalogQueryHandler

# Notification handlers
from .get_notification_preferences_query_handler import (
    GetNotificationPreferencesQueryHandler,
)
from .get_saved_suggestions_query_handler import GetSavedSuggestionsQueryHandler
from .get_streak_query_handler import GetStreakQueryHandler
from .get_user_by_firebase_uid_query_handler import GetUserByFirebaseUidQueryHandler
from .get_user_metrics_query_handler import GetUserMetricsQueryHandler
from .get_user_onboarding_status_query_handler import (
    GetUserOnboardingStatusQueryHandler,
)

# User handlers
from .get_user_profile_query_handler import GetUserProfileQueryHandler

# TDEE handlers
from .get_user_tdee_query_handler import GetUserTdeeQueryHandler
from .get_user_timezone_query_handler import GetUserTimezoneQueryHandler
from .get_weekly_auto_adjust_query_handler import GetWeeklyAutoAdjustQueryHandler
from .get_weekly_budget_query_handler import GetWeeklyBudgetQueryHandler
from .get_weekly_hydration_query_handler import GetWeeklyHydrationQueryHandler

# Food handlers
from .lookup_barcode_query_handler import LookupBarcodeQueryHandler
from .preview_tdee_query_handler import PreviewTdeeQueryHandler
from .search_foods_query_handler import SearchFoodsQueryHandler

__all__ = [
    # TDEE
    "GetUserTdeeQueryHandler",
    "PreviewTdeeQueryHandler",
    # Food
    "SearchFoodsQueryHandler",
    "GetPopularStaplesQueryHandler",
    "GetFoodDetailsQueryHandler",
    "GetProviderFoodDetailsQueryHandler",
    "LookupBarcodeQueryHandler",
    # Meal
    "GetMealByIdQueryHandler",
    "GetDailyMacrosQueryHandler",
    # User
    "GetUserProfileQueryHandler",
    "GetUserTimezoneQueryHandler",
    "GetWeeklyAutoAdjustQueryHandler",
    "GetBodyFatVisualProfileQueryHandler",
    "GetUserByFirebaseUidQueryHandler",
    "GetUserOnboardingStatusQueryHandler",
    "GetUserMetricsQueryHandler",
    # Activity
    "GetDailyActivitiesQueryHandler",
    "GetBulkActivitiesQueryHandler",
    "GetMovementCatalogQueryHandler",
    "GetDailyMovementQueryHandler",
    # Meal Plan
    "GetMealsByDateQueryHandler",
    # Progress
    "GetStreakQueryHandler",
    "GetDailyBreakdownQueryHandler",
    # Notification
    "GetNotificationPreferencesQueryHandler",
    "GetWeeklyBudgetQueryHandler",
    # Saved suggestion
    "GetSavedSuggestionsQueryHandler",
    # Hydration
    "GetDailyHydrationQueryHandler",
    "GetDrinkCatalogQueryHandler",
    "GetWeeklyHydrationQueryHandler",
    "GetJourneyProgressQueryHandler",
]
