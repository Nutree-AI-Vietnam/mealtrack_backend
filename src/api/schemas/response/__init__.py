"""
Response DTOs for API endpoints.
"""

# Daily nutrition responses
from .body_fat_visual_responses import (
    BodyFatVisualProfileResponse,
    BodyFatVisualSelectionResponse,
)
from .daily_nutrition_response import (
    DailyNutritionResponse,
)
from .daily_nutrition_response import (
    MacrosResponse as DailyMacrosResponse,
)

# Ingredient recognition responses
from .ingredient_recognition_responses import (
    IngredientCategoryEnum,
    IngredientRecognitionResponse,
)

# Ingredient responses
# Macros responses
# Meal responses
from .meal_responses import (
    DetailedMealResponse,
    FavoriteMealActionResponse,
    FavoriteMealsListResponse,
    FoodItemResponse,
    FoodLabelMetadataResponse,
    FoodLabelServingSizeResponse,
    MacrosResponse,
    ManualMealCreationResponse,
    MealListResponse,
    MealPhotoAnalysisResponse,
    MealSearchResponse,
    MealStatusEnum,
    MealValueInsightsStatusResponse,
    NutritionResponse,
    NutritionSummaryResponse,
    RecentMealsListResponse,
    SimpleMealResponse,
)

from .meal_suggestion_responses import (
    MacrosSchema as MealSuggestionMacrosSchema,
)

# Meal suggestion responses
from .meal_suggestion_responses import (
    MealSuggestionItem,
)

# Onboarding responses
from .onboarding_responses import (
    OnboardingFieldResponse,
    OnboardingResponse,
    OnboardingResponseResponse,
    OnboardingSectionResponse,
    OnboardingSectionsResponse,
)

# TDEE responses
from .tdee_responses import (
    TDEE_PREVIEW_CALCULATION_CONTRACT,
    BatchTdeeCalculationResponse,
    MacroTargetsResponse,
    TdeeCalculationResponse,
    TdeeComparisonResponse,
    TdeeErrorResponse,
    TdeeHistoryResponse,
)

# User responses
from .user_responses import (
    UserMetricsResponse,
    UserProfileResponse,
    UserStatusResponse,
    UserSyncResponse,
    UserUpdateResponse,
)

__all__ = [
    # Meal
    "SimpleMealResponse",
    "DetailedMealResponse",
    "MealListResponse",
    "RecentMealsListResponse",
    "FavoriteMealsListResponse",
    "FavoriteMealActionResponse",
    "MealPhotoAnalysisResponse",
    "MealSearchResponse",

    "NutritionSummaryResponse",
    "MacrosResponse",
    "NutritionResponse",
    "FoodItemResponse",
    "FoodLabelMetadataResponse",
    "FoodLabelServingSizeResponse",
    "ManualMealCreationResponse",
    "MealValueInsightsStatusResponse",
    "MealStatusEnum",
    # TDEE
    "TdeeCalculationResponse",
    "BatchTdeeCalculationResponse",
    "TdeeComparisonResponse",
    "TdeeHistoryResponse",
    "TdeeErrorResponse",
    "MacroTargetsResponse",
    "TDEE_PREVIEW_CALCULATION_CONTRACT",
    # Meal suggestion
    "MealSuggestionItem",
    "MealSuggestionMacrosSchema",
    # Onboarding
    "OnboardingFieldResponse",
    "OnboardingSectionResponse",
    "OnboardingSectionsResponse",
    "OnboardingResponseResponse",
    "OnboardingResponse",
    # Daily nutrition
    "DailyNutritionResponse",
    "DailyMacrosResponse",
    # User
    "UserProfileResponse",
    "UserSyncResponse",
    "UserStatusResponse",
    "UserUpdateResponse",
    "UserMetricsResponse",
    "BodyFatVisualProfileResponse",
    "BodyFatVisualSelectionResponse",
    # Ingredient recognition
    "IngredientRecognitionResponse",
    "IngredientCategoryEnum",
]
