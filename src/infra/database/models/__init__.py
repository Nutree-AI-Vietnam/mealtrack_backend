"""
Database models package.

This module imports all database models from their respective submodules
to provide a centralized access point.
"""

# Base models
from .base import BaseMixin, PrimaryEntityMixin, SecondaryEntityMixin, TimestampMixin

# Cheat days
from .cheat_day.cheat_day import CheatDayORM
from .daily_target_snapshot import DailyTargetSnapshotORM

# Enums
from .enums import (
    DietaryPreferenceEnum,
    FitnessGoalEnum,
    GoalEnum,
    JobTypeEnum,
    MealStatusEnum,
    MealTypeEnum,
    PlanDurationEnum,
    SexEnum,
)

# Feature flags
from .feature_flag import FeatureFlag

# Food reference (evolved from barcode_products)
from .food_reference_model import FoodReferenceModel
from .food_reference_nutrient import FoodReferenceNutrientModel
from .food_reference_serving_size import FoodReferenceServingSizeModel
from .hydration_entry import HydrationEntryORM
from .meal.favorite_meal import FavoriteMealORM
from .meal.food_item_translation_model import FoodItemTranslationORM

# Meal models
from .meal.meal import MealORM
from .meal.meal_image import MealImageORM
from .meal.meal_instruction_step import MealInstructionStepORM

# Translation models (meals + food items)
from .meal.meal_translation_model import MealTranslationORM
from .meal_image_cache import MealImageCacheModel
from .meal_recommendation import (
    MealCatalogIngredientORM,
    MealCatalogORM,
    MealRecommendationOperationORM,
    MealRecommendationORM,
)
from .meal_write_operation import MealWriteOperationORM

# Notification models
from .notification import NotificationPreferencesORM
from .nutrition.food_item import FoodItemORM

# Nutrition models
from .nutrition.nutrition import NutritionORM
from .nutrition_integrity import (
    FoodReferenceIntegrityControlModel,
    FoodReferenceIntegrityEventModel,
)
from .pending_meal_image_resolution import PendingMealImageResolutionModel

# Saved suggestions
from .saved_suggestion import SavedSuggestionModel
from .saved_suggestion_item import SavedSuggestionItemModel
from .saved_suggestion_step import SavedSuggestionStepModel
from .serving_phrase_translation import ServingPhraseTranslationModel
from .subscription import Subscription
from .user.body_fat_visual_profile import BodyFatVisualProfile
from .user.profile import UserProfile
from .user.profile_preference import UserProfilePreference

# User models
from .user.user import User

# Weekly budgets
from .weekly.weekly_macro_budget import WeeklyMacroBudgetORM

# Backward-compatible alias
BarcodeProductModel = FoodReferenceModel

# AI Handshake guest trial quota
from .ai_handshake_guest_trial_quota import AiHandshakeGuestTrialQuota

# Chat coach
from .chat import (
    ChatKnowledgeChunkORM,
    ChatKnowledgeDocumentORM,
    ChatMessageORM,
    ChatThreadORM,
)

# Durable mutation replay
from .durable_write_record import DurableWriteRecordORM

# Movement tracking
from .movement_entry import MovementEntryORM

# Promo codes (email marketing)
from .promo_code import PromoCode, PromoCodeRedemption
from .referral import PayoutRequest, ReferralCode, ReferralConversion, ReferralWallet
from .web_funnel_claim import (
    WebFunnelClaim,
    WebFunnelLead,
    WebFunnelOutbox,
    WebFunnelProviderEvent,
    WebFunnelRedemption,
)

# Weight tracking
from .weight_entry import WeightEntryORM

__all__ = [
    # Base
    "BaseMixin",
    "PrimaryEntityMixin",
    "SecondaryEntityMixin",
    "TimestampMixin",
    # Enums
    "MealStatusEnum",
    "DietaryPreferenceEnum",
    "FitnessGoalEnum",
    "MealTypeEnum",
    "PlanDurationEnum",
    "JobTypeEnum",
    "SexEnum",
    "GoalEnum",
    # User models
    "User",
    "UserProfile",
    "BodyFatVisualProfile",
    "UserProfilePreference",
    "Subscription",
    # Nutrition models
    "NutritionORM",
    "FoodItemORM",
    # Meal models
    "MealORM",
    "MealImageORM",
    "MealInstructionStepORM",
    "FavoriteMealORM",
    "MealWriteOperationORM",
    "MealTranslationORM",
    "FoodItemTranslationORM",
    # Test models
    # Notification models
    "NotificationPreferencesORM",
    # Feature flags
    "FeatureFlag",
    # Saved suggestions
    "SavedSuggestionModel",
    "SavedSuggestionItemModel",
    "SavedSuggestionStepModel",
    # Weekly budgets
    "WeeklyMacroBudgetORM",
    # Cheat days
    "CheatDayORM",
    "DailyTargetSnapshotORM",
    # Food reference (evolved from barcode_products)
    "FoodReferenceModel",
    "FoodReferenceNutrientModel",
    "FoodReferenceServingSizeModel",
    "ServingPhraseTranslationModel",
    "FoodReferenceIntegrityControlModel",
    "FoodReferenceIntegrityEventModel",
    "BarcodeProductModel",  # backward-compatible alias
    "HydrationEntryORM",
    "MealImageCacheModel",
    # Meal recommendation catalog
    "MealCatalogORM",
    "MealCatalogIngredientORM",
    "MealRecommendationORM",
    "MealRecommendationOperationORM",
    "PendingMealImageResolutionModel",
    # Referral system
    "ReferralCode",
    "ReferralConversion",
    "ReferralWallet",
    "PayoutRequest",
    # Weight tracking
    "WeightEntryORM",
    # Durable mutation replay
    "DurableWriteRecordORM",
    # Movement tracking
    "MovementEntryORM",
    # Promo codes
    "PromoCode",
    "PromoCodeRedemption",
    # AI Handshake guest trial quota
    "AiHandshakeGuestTrialQuota",
    "WebFunnelLead",
    "ChatThreadORM",
    "ChatMessageORM",
    "ChatKnowledgeDocumentORM",
    "ChatKnowledgeChunkORM",
    "WebFunnelClaim",
    "WebFunnelOutbox",
    "WebFunnelProviderEvent",
    "WebFunnelRedemption",
]
