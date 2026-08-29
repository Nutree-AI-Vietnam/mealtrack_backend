"""
command_handlers - Individual handler files.
Each handler is in its own file for better maintainability.
"""

from .add_custom_ingredient_command_handler import AddCustomIngredientCommandHandler
from .attach_meal_photo_command_handler import AttachMealPhotoCommandHandler
from .complete_onboarding_command_handler import CompleteOnboardingCommandHandler

# Standalone handlers (already individual files)
from .create_manual_meal_command_handler import CreateManualMealCommandHandler
from .delete_hydration_entry_command_handler import DeleteHydrationEntryCommandHandler
from .delete_meal_command_handler import DeleteMealCommandHandler
from .delete_meal_photo_command_handler import DeleteMealPhotoCommandHandler
from .delete_movement_entry_command_handler import DeleteMovementEntryCommandHandler
from .delete_user_command_handler import DeleteUserCommandHandler

# Meal handlers (already extracted)
from .edit_meal_command_handler import EditMealCommandHandler
from .log_caloric_drink_command_handler import LogCaloricDrinkCommandHandler

# Hydration handlers
from .log_hydration_command_handler import LogHydrationCommandHandler

# Movement handlers
from .log_movement_command_handler import LogMovementCommandHandler
from .meal_suggestion import (
    DiscoverMealsCommandHandler,
    GenerateMealRecipesCommandHandler,
    SaveMealSuggestionCommandHandler,
)
from .parse_meal_text_handler import ParseMealTextHandler

# Ingredient handlers
from .recognize_ingredient_command_handler import RecognizeIngredientCommandHandler

# Notification handlers
from .save_body_fat_visual_profile_command_handler import (
    SaveBodyFatVisualProfileCommandHandler,
)

# User handlers (newly extracted)
from .save_user_onboarding_command_handler import SaveUserOnboardingCommandHandler
from .saved_suggestion import (
    DeleteSavedSuggestionCommandHandler,
    SaveSuggestionCommandHandler,
)
from .scan_by_url_command_handler import ScanByUrlCommandHandler
from .sync_user_command_handler import SyncUserCommandHandler
from .update_custom_macros_command_handler import UpdateCustomMacrosCommandHandler
from .update_language_command_handler import UpdateLanguageCommandHandler
from .update_movement_entry_command_handler import UpdateMovementEntryCommandHandler
from .update_notification_preferences_command_handler import (
    UpdateNotificationPreferencesCommandHandler,
)
from .update_timezone_command_handler import UpdateTimezoneCommandHandler
from .update_weekly_auto_adjust_command_handler import (
    UpdateWeeklyAutoAdjustCommandHandler,
)
from .update_user_last_accessed_command_handler import (
    UpdateUserLastAccessedCommandHandler,
)
from .update_user_metrics_command_handler import UpdateUserMetricsCommandHandler
from .upload_meal_image_immediately_command_handler import (
    UploadMealImageImmediatelyHandler,
)

__all__ = [
    # Meal handlers
    "EditMealCommandHandler",
    "AddCustomIngredientCommandHandler",
    "AttachMealPhotoCommandHandler",
    "DeleteMealPhotoCommandHandler",
    "DeleteMealCommandHandler",
    # User handlers
    "SaveUserOnboardingCommandHandler",
    "SaveBodyFatVisualProfileCommandHandler",
    "SyncUserCommandHandler",
    "UpdateUserLastAccessedCommandHandler",
    "CompleteOnboardingCommandHandler",
    "DeleteUserCommandHandler",
    "UpdateUserMetricsCommandHandler",
    "UpdateCustomMacrosCommandHandler",
    "UpdateLanguageCommandHandler",
    "UpdateTimezoneCommandHandler",
    "UpdateWeeklyAutoAdjustCommandHandler",
    # Meal Suggestion handlers
    "DiscoverMealsCommandHandler",
    "GenerateMealRecipesCommandHandler",
    "SaveMealSuggestionCommandHandler",
    # Standalone handlers
    "CreateManualMealCommandHandler",
    "ParseMealTextHandler",
    "UploadMealImageImmediatelyHandler",
    "ScanByUrlCommandHandler",
    # Notification handlers
    "UpdateNotificationPreferencesCommandHandler",
    # Ingredient handlers
    "RecognizeIngredientCommandHandler",
    # Saved suggestion handlers
    "SaveSuggestionCommandHandler",
    "DeleteSavedSuggestionCommandHandler",
    # Hydration handlers
    "LogHydrationCommandHandler",
    "LogCaloricDrinkCommandHandler",
    "DeleteHydrationEntryCommandHandler",
    # Movement handlers
    "LogMovementCommandHandler",
    "DeleteMovementEntryCommandHandler",
    "UpdateMovementEntryCommandHandler",
]
