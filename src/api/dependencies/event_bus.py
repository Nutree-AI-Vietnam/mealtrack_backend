"""
Event bus dependency for FastAPI with proper type registrations.
"""

import logging

from src.app.commands.cheat_day import MarkCheatDayCommand, UnmarkCheatDayCommand
from src.app.commands.ingredient import RecognizeIngredientCommand

# Import all commands
from src.app.commands.meal import (
    AddCustomIngredientCommand,
    AttachMealPhotoCommand,
    DeleteMealCommand,
    DeleteMealPhotoCommand,
    EditMealCommand,
    ScanByUrlCommand,
    UploadMealImageImmediatelyCommand,
)
from src.app.commands.meal.create_manual_meal_command import CreateManualMealCommand
from src.app.commands.meal.parse_meal_text_command import ParseMealTextCommand
from src.app.commands.meal_catalog import LogCatalogMealCommand
from src.app.commands.meal_recommendation import (
    CreateThreeDayMealRecommendationCommand,
    LogRecommendedMealCommand,
    SkipMealRecommendationSlotCommand,
    SwapMealRecommendationSlotCommand,
)
from src.app.commands.meal_suggestion import (
    DiscoverMealsCommand,
    GenerateMealRecipesCommand,
    SaveMealSuggestionCommand,
)
from src.app.commands.movement import (
    DeleteMovementEntryCommand,
    LogMovementCommand,
    UpdateMovementEntryCommand,
)
from src.app.commands.notification import (
    UpdateNotificationPreferencesCommand,
)
from src.app.commands.saved_suggestion import (
    DeleteSavedSuggestionCommand,
    SaveSuggestionCommand,
)
from src.app.commands.user import (
    CompleteOnboardingCommand,
    DeleteUserCommand,
    SaveBodyFatVisualProfileCommand,
    SaveUserOnboardingCommand,
    UpdateCustomMacrosCommand,
    UpdateLanguageCommand,
    UpdateTimezoneCommand,
)
from src.app.commands.user.sync_user_command import (
    SyncUserCommand,
    UpdateUserLastAccessedCommand,
)
from src.app.commands.user.update_user_metrics_command import UpdateUserMetricsCommand
from src.app.commands.weight import (
    AddWeightEntryCommand,
    DeleteWeightEntryCommand,
    SyncWeightEntriesCommand,
)

# Import all command handlers from module
# Ingredient handlers
# Saved suggestion handlers
from src.app.handlers.command_handlers import (
    AddCustomIngredientCommandHandler,
    AttachMealPhotoCommandHandler,
    CompleteOnboardingCommandHandler,
    CreateManualMealCommandHandler,
    DeleteMealCommandHandler,
    DeleteMealPhotoCommandHandler,
    DeleteMovementEntryCommandHandler,
    DeleteSavedSuggestionCommandHandler,
    DeleteUserCommandHandler,
    DiscoverMealsCommandHandler,
    EditMealCommandHandler,
    GenerateMealRecipesCommandHandler,
    LogMovementCommandHandler,
    ParseMealTextHandler,
    RecognizeIngredientCommandHandler,
    SaveBodyFatVisualProfileCommandHandler,
    SaveMealSuggestionCommandHandler,
    SaveSuggestionCommandHandler,
    SaveUserOnboardingCommandHandler,
    ScanByUrlCommandHandler,
    SyncUserCommandHandler,
    UpdateCustomMacrosCommandHandler,
    UpdateLanguageCommandHandler,
    UpdateMovementEntryCommandHandler,
    UpdateNotificationPreferencesCommandHandler,
    UpdateTimezoneCommandHandler,
    UpdateUserLastAccessedCommandHandler,
    UpdateUserMetricsCommandHandler,
    UploadMealImageImmediatelyHandler,
)
from src.app.handlers.command_handlers.add_weight_entry_command_handler import (
    AddWeightEntryCommandHandler,
)
from src.app.handlers.command_handlers.delete_weight_entry_command_handler import (
    DeleteWeightEntryCommandHandler,
)
from src.app.handlers.command_handlers.mark_cheat_day_command_handler import (
    MarkCheatDayCommandHandler,
)
from src.app.handlers.command_handlers.meal_catalog import (
    LogCatalogMealCommandHandler,
)
from src.app.handlers.command_handlers.meal_recommendation import (
    CreateThreeDayMealRecommendationCommandHandler,
    LogRecommendedMealCommandHandler,
    SkipMealRecommendationSlotCommandHandler,
    SwapMealRecommendationSlotCommandHandler,
)
from src.app.handlers.command_handlers.sync_weight_entries_command_handler import (
    SyncWeightEntriesCommandHandler,
)
from src.app.handlers.command_handlers.unmark_cheat_day_command_handler import (
    UnmarkCheatDayCommandHandler,
)

# Import all query handlers from module
from src.app.handlers.query_handlers import (
    GetBodyFatVisualProfileQueryHandler,
    GetBulkActivitiesQueryHandler,
    GetDailyActivitiesQueryHandler,
    GetDailyBreakdownQueryHandler,
    GetDailyMacrosQueryHandler,
    GetDailyMovementQueryHandler,
    GetFoodDetailsQueryHandler,
    GetJourneyProgressQueryHandler,
    GetMealByIdQueryHandler,
    GetMealsByDateQueryHandler,
    GetMovementCatalogQueryHandler,
    GetNotificationPreferencesQueryHandler,
    GetPopularStaplesQueryHandler,
    GetProviderFoodDetailsQueryHandler,
    GetSavedSuggestionsQueryHandler,
    GetStreakQueryHandler,
    GetUserByFirebaseUidQueryHandler,
    GetUserMetricsQueryHandler,
    GetUserOnboardingStatusQueryHandler,
    GetUserProfileQueryHandler,
    GetUserTdeeQueryHandler,
    GetUserTimezoneQueryHandler,
    GetWeeklyBudgetQueryHandler,
    LookupBarcodeQueryHandler,
    PreviewTdeeQueryHandler,
    SearchFoodsQueryHandler,
)
from src.app.handlers.query_handlers.get_activities_presence_query_handler import (
    GetActivitiesPresenceQueryHandler,
)
from src.app.handlers.query_handlers.get_cheat_days_query_handler import (
    GetCheatDaysQueryHandler,
)
from src.app.handlers.query_handlers.get_meal_recommendation_plan_query_handler import (
    GetMealRecommendationPlanQueryHandler,
)
from src.app.handlers.query_handlers.get_meal_recommendation_slot_detail_query_handler import (
    GetMealRecommendationSlotDetailQueryHandler,
)
from src.app.handlers.query_handlers.get_nutrition_bulk_query_handler import (
    GetNutritionBulkQueryHandler,
)
from src.app.handlers.query_handlers.get_weight_entries_query_handler import (
    GetWeightEntriesQueryHandler,
)
from src.app.handlers.query_handlers.list_logged_catalog_meals_query_handler import (
    ListLoggedCatalogMealsQueryHandler,
)
from src.app.queries.activity import GetBulkActivitiesQuery, GetDailyActivitiesQuery
from src.app.queries.cheat_day import GetCheatDaysQuery
from src.app.queries.food.get_food_details_query import GetFoodDetailsQuery
from src.app.queries.food.get_popular_staples_query import GetPopularStaplesQuery
from src.app.queries.food.get_provider_food_details_query import (
    GetProviderFoodDetailsQuery,
)
from src.app.queries.food.lookup_barcode_query import LookupBarcodeQuery
from src.app.queries.food.search_foods_query import SearchFoodsQuery
from src.app.queries.get_weekly_budget_query import GetWeeklyBudgetQuery

# Import all queries
from src.app.queries.meal import (
    GetDailyBreakdownQuery,
    GetDailyMacrosQuery,
    GetMealByIdQuery,
    GetMealsByDateQuery,
    GetStreakQuery,
)
from src.app.queries.meal_catalog import ListLoggedCatalogMealsQuery
from src.app.queries.meal_recommendation import (
    GetMealRecommendationPlanQuery,
    GetMealRecommendationSlotDetailQuery,
)
from src.app.queries.movement import GetDailyMovementQuery, GetMovementCatalogQuery
from src.app.queries.notification import GetNotificationPreferencesQuery
from src.app.queries.nutrition import GetActivitiesPresenceQuery, GetNutritionBulkQuery
from src.app.queries.progress import GetJourneyProgressQuery
from src.app.queries.saved_suggestion import GetSavedSuggestionsQuery
from src.app.queries.tdee import GetUserTdeeQuery, PreviewTdeeQuery
from src.app.queries.user import (
    GetBodyFatVisualProfileQuery,
    GetUserMetricsQuery,
    GetUserProfileQuery,
    GetUserTimezoneQuery,
)
from src.app.queries.user.get_user_by_firebase_uid_query import (
    GetUserByFirebaseUidQuery,
)
from src.app.queries.user.get_user_onboarding_status_query import (
    GetUserOnboardingStatusQuery,
)
from src.app.queries.weight import GetWeightEntriesQuery
from src.app.services.meal_recommendation_history_projector import (
    MealRecommendationHistoryProjector,
)
from src.bootstrap.integration_services import get_integration_event_publisher
from src.domain.ports.food_reference_repository_port import (
    FoodReferenceSearchProjection,
)
from src.domain.services.nutrition_integrity_policy import NutritionIntegrityPolicy
from src.infra.cache.provider_budget import MemoryProviderBudget, RedisProviderBudget
from src.infra.config.settings import settings
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.event_bus import EventBus, PyMediatorEventBus

logger = logging.getLogger(__name__)

# Singleton event buses
_food_search_event_bus: EventBus | None = None
_configured_event_bus: EventBus | None = None


def _build_provider_budget(cache_service):
    """Build the shared provider budget. Production fail-closes without Redis."""
    if settings.NUTRITION_PROVIDER_GLOBAL_RPM is None:
        logger.error(
            "NUTRITION_PROVIDER_GLOBAL_RPM is unset; provider-origin meal saves "
            "will return NUTRITION_PROVIDER_UNAVAILABLE"
        )
        return None
    redis_client = getattr(cache_service, "redis", None)
    if redis_client is not None:
        return RedisProviderBudget(redis_client)
    if settings.ENVIRONMENT.lower() == "development":
        logger.warning(
            "Redis cache unavailable; using process-local provider budget in development"
        )
        return MemoryProviderBudget()
    logger.error(
        "Redis cache unavailable; provider-origin meal saves will return "
        "NUTRITION_PROVIDER_UNAVAILABLE"
    )
    return None


async def _search_local_food_references(
    query: str,
    region: str,
    limit: int,
) -> list[FoodReferenceSearchProjection]:
    async with AsyncUnitOfWork() as uow:
        return await uow.food_references.search_local(query, region, limit)


async def _load_popular_staple_food_references(
    identities: list[tuple[str, str]],
) -> list[dict]:
    async with AsyncUnitOfWork() as uow:
        return await uow.food_references.get_by_source_identities(identities)


async def _food_integrity_cache_context() -> dict[str, int | str]:
    """Read DB-owned cache namespace before any food-search cache access."""
    async with AsyncUnitOfWork() as uow:
        control = await uow.food_reference_integrity.get_active_control()
        return {
            "policy_version": control.active_policy_version,
            "generation": control.catalog_integrity_generation,
        }


def get_food_search_event_bus() -> EventBus:
    """
    Get a lightweight event bus for food search operations (singleton).

    This event bus only registers food-related handlers and does NOT
    initialize heavy services like Cloudinary, AI providers, etc.

    Returns:
        EventBus: Lightweight event bus for food search
    """
    global _food_search_event_bus
    if _food_search_event_bus is not None:
        return _food_search_event_bus

    from src.api.base_dependencies import (
        get_fat_secret_service_instance,
        get_food_cache_service,
        get_food_data_service,
        get_food_mapping_service,
        get_open_food_facts_service_instance,
        get_text_translation_service,
    )
    from src.domain.services.meal_suggestion.macro_validation_service import (
        MacroValidationService,
    )
    from src.infra.adapters.brave_search_nutrition_service import (
        get_brave_search_nutrition_service,
    )

    event_bus = PyMediatorEventBus()

    # Only register food-related handlers (lightweight)
    food_cache_service = get_food_cache_service()
    food_data_service = get_food_data_service()
    food_mapping_service = get_food_mapping_service()
    open_food_facts_service = get_open_food_facts_service_instance()
    fat_secret_service = get_fat_secret_service_instance()

    # Translation service for localized food search (optional OpenAI-backed)
    text_translation_service = get_text_translation_service()

    # Barcode cascade: Brave Search is optional — None if keys are not set.
    macro_validation_service = MacroValidationService()
    from src.infra.adapters.meal_generation_service import MealGenerationService

    meal_generation_service = MealGenerationService()
    brave_search_service = get_brave_search_nutrition_service(
        meal_generation_service=meal_generation_service,
        macro_validation_service=macro_validation_service,
    )

    event_bus.register_handler(
        SearchFoodsQuery,
        SearchFoodsQueryHandler(
            food_cache_service,
            food_mapping_service,
            fat_secret_service=fat_secret_service,
            translation_service=text_translation_service,
            local_search=_search_local_food_references,
            integrity_context=_food_integrity_cache_context,
            uow_factory=AsyncUnitOfWork,
        ),
    )
    event_bus.register_handler(
        GetPopularStaplesQuery,
        GetPopularStaplesQueryHandler(
            food_mapping_service,
            _load_popular_staple_food_references,
        ),
    )
    event_bus.register_handler(
        GetFoodDetailsQuery,
        GetFoodDetailsQueryHandler(
            food_data_service, food_cache_service, food_mapping_service
        ),
    )
    event_bus.register_handler(
        GetProviderFoodDetailsQuery,
        GetProviderFoodDetailsQueryHandler(
            food_mapping_service,
            fat_secret_service=fat_secret_service,
            uow_factory=AsyncUnitOfWork,
            cache_service=food_cache_service,
        ),
    )
    event_bus.register_handler(
        LookupBarcodeQuery,
        LookupBarcodeQueryHandler(
            open_food_facts_service=open_food_facts_service,
            fat_secret_service=fat_secret_service,
            async_uow_factory=AsyncUnitOfWork,
            translation_service=text_translation_service,
            brave_search_service=brave_search_service,
            meal_generation_service=meal_generation_service,
            macro_validation_service=macro_validation_service,
            food_data_service=food_data_service,
            food_mapping_service=food_mapping_service,
        ),
    )

    _food_search_event_bus = event_bus
    return _food_search_event_bus


def get_configured_event_bus() -> EventBus:
    """
    Get a singleton event bus with all handlers configured.

    This is now a singleton to prevent memory leaks from creating new event buses
    and dynamically generated handler classes on every request.

    Handlers receive fresh async Unit of Work instances while the event bus is reused.

    Returns:
        EventBus: Singleton event bus instance
    """
    global _configured_event_bus

    if _configured_event_bus is not None:
        return _configured_event_bus

    # Get singleton services (these are safe to reuse)
    from src.api.base_dependencies import (
        get_cache_service,
        get_fat_secret_service_instance,
        get_food_cache_service,
        get_food_data_service,
        get_food_mapping_service,
        get_gpt_parser,
        get_image_store,
        get_meal_analyze_graph_settings,
        get_meal_translation_service,
        get_parse_text_settings,
        get_suggestion_orchestration_service,
        get_text_translation_service,
        get_vision_service,
    )

    image_store = get_image_store()
    vision_service = get_vision_service()
    gpt_parser = get_gpt_parser()
    food_cache_service = get_food_cache_service()
    food_data_service = get_food_data_service()
    food_mapping_service = get_food_mapping_service()
    fat_secret_service = get_fat_secret_service_instance()
    cache_service = get_cache_service()
    suggestion_service = get_suggestion_orchestration_service()

    from src.app.services.food_reference_validation_service import (
        FoodReferenceValidationService,
    )
    from src.app.services.meal_analyze_workflow import MealAnalyzeWorkflow
    from src.domain.services.meal_recommendation.three_day_plan_optimizer import (
        ThreeDayPlanOptimizer,
    )
    from src.infra.database.uow_async import AsyncUnitOfWork

    queue_publisher = get_integration_event_publisher()
    provider_budget = _build_provider_budget(cache_service)
    nutrition_integrity_policy = NutritionIntegrityPolicy()

    event_bus = PyMediatorEventBus()
    from src.api.base_dependencies import get_catalog_meal_snapshot_service

    recommendation_snapshot = get_catalog_meal_snapshot_service()
    recommendation_history = MealRecommendationHistoryProjector()
    graph_settings = get_meal_analyze_graph_settings()

    async def find_food_references_by_normalized_names(
        normalized_names: list[str],
    ) -> dict:
        async with AsyncUnitOfWork() as uow:
            return await uow.food_references.find_batch_by_normalized_names(
                normalized_names
            )

    food_reference_validation_service = FoodReferenceValidationService(
        food_reference_batch_lookup=find_food_references_by_normalized_names,
        nutrition_reference_provider=fat_secret_service,
        timeout_seconds=graph_settings["external_provider_timeout_seconds"],
        integrity_policy=nutrition_integrity_policy,
    )
    meal_analyze_workflow = MealAnalyzeWorkflow(
        food_reference_validation_service=food_reference_validation_service,
        fatsecret_validation_enabled=graph_settings["fatsecret_validation_enabled"],
        graph_version=graph_settings["graph_version"],
    )
    parse_text_settings = get_parse_text_settings()

    # Register meal command handlers
    # Handlers receive AsyncUnitOfWork (concrete) and event_bus at the composition root
    meal_translation_service = get_meal_translation_service()
    text_translation_service = get_text_translation_service()
    from src.infra.adapters.meal_generation_service import MealGenerationService

    meal_generation_service = MealGenerationService()

    event_bus.register_handler(
        UploadMealImageImmediatelyCommand,
        UploadMealImageImmediatelyHandler(
            uow=AsyncUnitOfWork(),
            event_bus=event_bus,
            image_store=image_store,
            vision_service=vision_service,
            gpt_parser=gpt_parser,
            meal_translation_service=meal_translation_service,
            event_publisher=queue_publisher,
            environment=settings.ENVIRONMENT,
            meal_analyze_workflow=meal_analyze_workflow,
            meal_analyze_graph_enabled=graph_settings["graph_enabled"],
        ),
    )

    async def _download_image_bytes_pooled(image_url: str) -> bytes:
        from src.infra.http import get_shared_http_client

        client = get_shared_http_client()
        resp = await client.get(image_url, timeout=30.0)
        resp.raise_for_status()
        return resp.content

    event_bus.register_handler(
        ScanByUrlCommand,
        ScanByUrlCommandHandler(
            uow=AsyncUnitOfWork(),
            event_bus=event_bus,
            vision_service=vision_service,
            gpt_parser=gpt_parser,
            meal_translation_service=meal_translation_service,
            text_translation_service=text_translation_service,
            event_publisher=queue_publisher,
            environment=settings.ENVIRONMENT,
            meal_analyze_workflow=meal_analyze_workflow,
            meal_analyze_graph_enabled=graph_settings["graph_enabled"],
            download_image_bytes=_download_image_bytes_pooled,
        ),
    )

    # Register meal edit command handlers
    event_bus.register_handler(
        EditMealCommand,
        EditMealCommandHandler(
            uow=AsyncUnitOfWork(),
            uow_factory=AsyncUnitOfWork,
            event_publisher=queue_publisher,
            event_bus=event_bus,
            environment=settings.ENVIRONMENT,
            provider=fat_secret_service,
            provider_budget=provider_budget,
            provider_rpm=settings.NUTRITION_PROVIDER_GLOBAL_RPM,
        ),
    )

    event_bus.register_handler(
        AddCustomIngredientCommand,
        AddCustomIngredientCommandHandler(
            uow=AsyncUnitOfWork(),
            event_publisher=queue_publisher,
            environment=settings.ENVIRONMENT,
        ),
    )

    event_bus.register_handler(
        AttachMealPhotoCommand,
        AttachMealPhotoCommandHandler(
            uow=AsyncUnitOfWork(),
            event_publisher=queue_publisher,
            environment=settings.ENVIRONMENT,
        ),
    )

    event_bus.register_handler(
        DeleteMealPhotoCommand,
        DeleteMealPhotoCommandHandler(
            uow=AsyncUnitOfWork(),
            event_publisher=queue_publisher,
            environment=settings.ENVIRONMENT,
        ),
    )

    event_bus.register_handler(
        DeleteMealCommand,
        DeleteMealCommandHandler(
            uow=AsyncUnitOfWork(),
            environment=settings.ENVIRONMENT,
            event_publisher=queue_publisher,
        ),
    )

    event_bus.register_handler(
        CreateManualMealCommand,
        CreateManualMealCommandHandler(
            uow=AsyncUnitOfWork(),
            uow_factory=AsyncUnitOfWork,
            event_publisher=queue_publisher,
            event_bus=event_bus,
            environment=settings.ENVIRONMENT,
            provider=fat_secret_service,
            provider_budget=provider_budget,
            provider_rpm=settings.NUTRITION_PROVIDER_GLOBAL_RPM,
        ),
    )

    # Register meal text parsing command handler
    event_bus.register_handler(
        ParseMealTextCommand,
        ParseMealTextHandler(
            meal_generation_service=meal_generation_service,
            fat_secret_service=fat_secret_service,
            translation_service=text_translation_service,
            food_reference_batch_lookup=find_food_references_by_normalized_names,
            structured_reference_enabled=parse_text_settings[
                "structured_reference_enabled"
            ],
            uow_factory=AsyncUnitOfWork,
        ),
    )

    # Register food database query handlers
    event_bus.register_handler(
        SearchFoodsQuery,
        SearchFoodsQueryHandler(
            food_cache_service,
            food_mapping_service,
            fat_secret_service=fat_secret_service,
            translation_service=text_translation_service,
            local_search=_search_local_food_references,
            integrity_context=_food_integrity_cache_context,
            uow_factory=AsyncUnitOfWork,
        ),
    )
    event_bus.register_handler(
        GetPopularStaplesQuery,
        GetPopularStaplesQueryHandler(
            food_mapping_service,
            _load_popular_staple_food_references,
        ),
    )
    event_bus.register_handler(
        GetFoodDetailsQuery,
        GetFoodDetailsQueryHandler(
            food_data_service, food_cache_service, food_mapping_service
        ),
    )
    event_bus.register_handler(
        GetProviderFoodDetailsQuery,
        GetProviderFoodDetailsQueryHandler(
            food_mapping_service,
            fat_secret_service=fat_secret_service,
            uow_factory=AsyncUnitOfWork,
            cache_service=food_cache_service,
        ),
    )

    # Register meal query handlers
    # These handlers now use UnitOfWork internally for fresh sessions
    event_bus.register_handler(GetMealByIdQuery, GetMealByIdQueryHandler())
    event_bus.register_handler(
        GetDailyMacrosQuery,
        GetDailyMacrosQueryHandler(
            cache_service=cache_service,
        ),
    )
    event_bus.register_handler(
        GetWeeklyBudgetQuery,
        GetWeeklyBudgetQueryHandler(cache_service=cache_service),
    )
    event_bus.register_handler(
        GetStreakQuery,
        GetStreakQueryHandler(cache_service=cache_service),
    )
    event_bus.register_handler(
        GetDailyBreakdownQuery,
        GetDailyBreakdownQueryHandler(cache_service=cache_service),
    )

    # Register bulk nutrition query handlers
    event_bus.register_handler(
        GetNutritionBulkQuery,
        GetNutritionBulkQueryHandler(cache_service=cache_service),
    )
    event_bus.register_handler(
        GetActivitiesPresenceQuery,
        GetActivitiesPresenceQueryHandler(cache_service=cache_service),
    )

    # Register activity query handlers
    event_bus.register_handler(
        GetDailyActivitiesQuery,
        GetDailyActivitiesQueryHandler(cache_service=cache_service),
    )
    event_bus.register_handler(
        GetBulkActivitiesQuery,
        GetBulkActivitiesQueryHandler(cache_service=cache_service),
    )
    event_bus.register_handler(
        GetMovementCatalogQuery,
        GetMovementCatalogQueryHandler(),
    )
    event_bus.register_handler(
        GetDailyMovementQuery,
        GetDailyMovementQueryHandler(),
    )
    event_bus.register_handler(
        LogMovementCommand,
        LogMovementCommandHandler(
            uow=AsyncUnitOfWork(),
            environment=settings.ENVIRONMENT,
            event_publisher=queue_publisher,
        ),
    )
    event_bus.register_handler(
        DeleteMovementEntryCommand,
        DeleteMovementEntryCommandHandler(
            uow=AsyncUnitOfWork(),
            environment=settings.ENVIRONMENT,
            event_publisher=queue_publisher,
        ),
    )
    event_bus.register_handler(
        UpdateMovementEntryCommand,
        UpdateMovementEntryCommandHandler(
            uow=AsyncUnitOfWork(),
            environment=settings.ENVIRONMENT,
            event_publisher=queue_publisher,
        ),
    )

    event_bus.register_handler(GetMealsByDateQuery, GetMealsByDateQueryHandler())

    event_bus.register_handler(
        CreateThreeDayMealRecommendationCommand,
        CreateThreeDayMealRecommendationCommandHandler(
            uow=AsyncUnitOfWork(),
            optimizer=ThreeDayPlanOptimizer(),
            catalog_snapshot_service=recommendation_snapshot,
        ),
    )
    event_bus.register_handler(
        SwapMealRecommendationSlotCommand,
        SwapMealRecommendationSlotCommandHandler(
            uow=AsyncUnitOfWork(),
            optimizer=ThreeDayPlanOptimizer(),
            catalog_snapshot_service=recommendation_snapshot,
            history_projector=recommendation_history,
        ),
    )
    event_bus.register_handler(
        LogRecommendedMealCommand,
        LogRecommendedMealCommandHandler(
            uow=AsyncUnitOfWork(),
            meal_translation_service=meal_translation_service,
            event_publisher=queue_publisher,
            event_bus=event_bus,
            environment=settings.ENVIRONMENT,
        ),
    )
    from src.api.base_dependencies import get_catalog_meal_browse_service
    from src.app.services.remaining_recommendation_recalculator import (
        RemainingRecommendationRecalculator,
    )

    event_bus.register_handler(
        LogCatalogMealCommand,
        LogCatalogMealCommandHandler(
            uow=AsyncUnitOfWork(),
            browse_service=get_catalog_meal_browse_service(),
            meal_translation_service=meal_translation_service,
            event_publisher=queue_publisher,
            event_bus=event_bus,
            environment=settings.ENVIRONMENT,
            recalculator=RemainingRecommendationRecalculator(
                AsyncUnitOfWork,
                optimizer=ThreeDayPlanOptimizer(),
                snapshot_service=recommendation_snapshot,
                history_projector=recommendation_history,
            ),
        ),
    )
    event_bus.register_handler(
        ListLoggedCatalogMealsQuery,
        ListLoggedCatalogMealsQueryHandler(
            AsyncUnitOfWork,
            recommendation_snapshot,
        ),
    )
    event_bus.register_handler(
        SkipMealRecommendationSlotCommand,
        SkipMealRecommendationSlotCommandHandler(uow=AsyncUnitOfWork()),
    )
    event_bus.register_handler(
        GetMealRecommendationPlanQuery,
        GetMealRecommendationPlanQueryHandler(AsyncUnitOfWork),
    )
    event_bus.register_handler(
        GetMealRecommendationSlotDetailQuery,
        GetMealRecommendationSlotDetailQueryHandler(AsyncUnitOfWork),
    )

    # Register meal suggestion handlers
    event_bus.register_handler(
        DiscoverMealsCommand,
        DiscoverMealsCommandHandler(suggestion_service),
    )
    event_bus.register_handler(
        GenerateMealRecipesCommand,
        GenerateMealRecipesCommandHandler(suggestion_service),
    )
    event_bus.register_handler(
        SaveMealSuggestionCommand,
        SaveMealSuggestionCommandHandler(
            uow=AsyncUnitOfWork(),
            event_publisher=queue_publisher,
            event_bus=event_bus,
            environment=settings.ENVIRONMENT,
        ),
    )

    # Register user handlers
    event_bus.register_handler(
        SaveUserOnboardingCommand,
        SaveUserOnboardingCommandHandler(
            uow=AsyncUnitOfWork(),
            environment=settings.ENVIRONMENT,
            event_publisher=queue_publisher,
        ),
    )
    event_bus.register_handler(
        SaveBodyFatVisualProfileCommand,
        SaveBodyFatVisualProfileCommandHandler(uow=AsyncUnitOfWork()),
    )
    event_bus.register_handler(SyncUserCommand, SyncUserCommandHandler())
    event_bus.register_handler(
        UpdateUserLastAccessedCommand, UpdateUserLastAccessedCommandHandler()
    )
    event_bus.register_handler(
        CompleteOnboardingCommand,
        CompleteOnboardingCommandHandler(
            uow=AsyncUnitOfWork(),
            environment=settings.ENVIRONMENT,
            event_publisher=queue_publisher,
        ),
    )
    event_bus.register_handler(
        DeleteUserCommand,
        DeleteUserCommandHandler(
            cache_service=cache_service,
            environment=settings.ENVIRONMENT,
            event_publisher=queue_publisher,
        ),
    )
    event_bus.register_handler(
        UpdateUserMetricsCommand,
        UpdateUserMetricsCommandHandler(
            uow=AsyncUnitOfWork(),
            environment=settings.ENVIRONMENT,
            event_publisher=queue_publisher,
        ),
    )
    event_bus.register_handler(
        UpdateTimezoneCommand,
        UpdateTimezoneCommandHandler(
            event_publisher=queue_publisher,
            environment=settings.ENVIRONMENT,
        ),
    )
    event_bus.register_handler(
        UpdateLanguageCommand,
        UpdateLanguageCommandHandler(
            event_publisher=queue_publisher,
            environment=settings.ENVIRONMENT,
        ),
    )
    event_bus.register_handler(
        UpdateCustomMacrosCommand,
        UpdateCustomMacrosCommandHandler(
            uow=AsyncUnitOfWork(),
            environment=settings.ENVIRONMENT,
            event_publisher=queue_publisher,
        ),
    )

    event_bus.register_handler(
        GetUserProfileQuery,
        GetUserProfileQueryHandler(cache_service=cache_service),
    )
    event_bus.register_handler(
        GetBodyFatVisualProfileQuery,
        GetBodyFatVisualProfileQueryHandler(uow=AsyncUnitOfWork()),
    )
    event_bus.register_handler(
        GetUserTimezoneQuery, GetUserTimezoneQueryHandler(AsyncUnitOfWork)
    )
    event_bus.register_handler(
        GetUserByFirebaseUidQuery, GetUserByFirebaseUidQueryHandler()
    )
    event_bus.register_handler(
        GetUserOnboardingStatusQuery, GetUserOnboardingStatusQueryHandler()
    )
    event_bus.register_handler(
        GetUserMetricsQuery, GetUserMetricsQueryHandler(cache_service=cache_service)
    )
    event_bus.register_handler(
        GetUserTdeeQuery, GetUserTdeeQueryHandler(cache_service=cache_service)
    )
    event_bus.register_handler(PreviewTdeeQuery, PreviewTdeeQueryHandler())

    # Register notification handlers
    event_bus.register_handler(
        UpdateNotificationPreferencesCommand,
        UpdateNotificationPreferencesCommandHandler(
            event_publisher=queue_publisher,
            environment=settings.ENVIRONMENT,
        ),
    )
    event_bus.register_handler(
        GetNotificationPreferencesQuery,
        GetNotificationPreferencesQueryHandler(),
    )

    # Register ingredient recognition handler
    event_bus.register_handler(
        RecognizeIngredientCommand,
        RecognizeIngredientCommandHandler(
            vision_service=vision_service,
            translation_service=text_translation_service,
        ),
    )

    # Register cheat day handlers
    event_bus.register_handler(
        MarkCheatDayCommand,
        MarkCheatDayCommandHandler(
            event_publisher=queue_publisher,
            environment=settings.ENVIRONMENT,
        ),
    )
    event_bus.register_handler(
        UnmarkCheatDayCommand,
        UnmarkCheatDayCommandHandler(
            event_publisher=queue_publisher,
            environment=settings.ENVIRONMENT,
        ),
    )
    event_bus.register_handler(GetCheatDaysQuery, GetCheatDaysQueryHandler())

    # Register weight entry handlers
    event_bus.register_handler(AddWeightEntryCommand, AddWeightEntryCommandHandler())
    event_bus.register_handler(
        DeleteWeightEntryCommand, DeleteWeightEntryCommandHandler()
    )
    event_bus.register_handler(
        SyncWeightEntriesCommand, SyncWeightEntriesCommandHandler()
    )
    event_bus.register_handler(GetWeightEntriesQuery, GetWeightEntriesQueryHandler())
    event_bus.register_handler(
        GetJourneyProgressQuery,
        GetJourneyProgressQueryHandler(
            uow=AsyncUnitOfWork(),
            cache_service=cache_service,
        ),
    )

    # Register hydration handlers
    from src.app.commands.hydration import (
        DeleteHydrationEntryCommand,
        LogCaloricDrinkCommand,
        LogHydrationCommand,
    )
    from src.app.handlers.command_handlers import (
        DeleteHydrationEntryCommandHandler,
        LogCaloricDrinkCommandHandler,
        LogHydrationCommandHandler,
    )
    from src.app.handlers.query_handlers import (
        GetDailyHydrationQueryHandler,
        GetDrinkCatalogQueryHandler,
        GetWeeklyHydrationQueryHandler,
    )
    from src.app.queries.hydration import (
        GetDailyHydrationQuery,
        GetDrinkCatalogQuery,
        GetWeeklyHydrationQuery,
    )

    event_bus.register_handler(
        LogHydrationCommand,
        LogHydrationCommandHandler(
            uow=AsyncUnitOfWork(),
            environment=settings.ENVIRONMENT,
            event_publisher=queue_publisher,
        ),
    )
    event_bus.register_handler(
        LogCaloricDrinkCommand,
        LogCaloricDrinkCommandHandler(
            uow=AsyncUnitOfWork(),
            environment=settings.ENVIRONMENT,
            event_publisher=queue_publisher,
        ),
    )
    event_bus.register_handler(
        DeleteHydrationEntryCommand,
        DeleteHydrationEntryCommandHandler(
            uow=AsyncUnitOfWork(),
            environment=settings.ENVIRONMENT,
            event_publisher=queue_publisher,
        ),
    )
    event_bus.register_handler(
        GetDailyHydrationQuery,
        GetDailyHydrationQueryHandler(cache_service=cache_service),
    )

    event_bus.register_handler(GetDrinkCatalogQuery, GetDrinkCatalogQueryHandler())
    event_bus.register_handler(
        GetWeeklyHydrationQuery,
        GetWeeklyHydrationQueryHandler(cache_service=cache_service),
    )

    # Register saved suggestion handlers
    event_bus.register_handler(
        SaveSuggestionCommand,
        SaveSuggestionCommandHandler(
            uow=AsyncUnitOfWork(),
            event_publisher=queue_publisher,
            environment=settings.ENVIRONMENT,
        ),
    )
    event_bus.register_handler(
        DeleteSavedSuggestionCommand,
        DeleteSavedSuggestionCommandHandler(
            event_publisher=queue_publisher,
            environment=settings.ENVIRONMENT,
        ),
    )
    event_bus.register_handler(
        GetSavedSuggestionsQuery,
        GetSavedSuggestionsQueryHandler(cache_service=cache_service),
    )

    _configured_event_bus = event_bus
    return _configured_event_bus
