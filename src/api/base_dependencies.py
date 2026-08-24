import logging
import os
from collections.abc import Callable
from importlib import import_module
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.services.catalog_food_reference_review_service import (
    CatalogFoodReferenceReviewService,
)
from src.app.services.catalog_meal_seed_import_service import CatalogMealSeedImporter
from src.app.services.catalog_meal_snapshot_service import CatalogMealSnapshotService
from src.app.services.meal_recommendation_analytics_service import (
    MealRecommendationAnalyticsService,
)
from src.domain.parsers.vision_response_parser import VisionResponseParser
from src.domain.ports.food_cache_service_port import FoodCacheServicePort
from src.domain.ports.food_mapping_service_port import FoodMappingServicePort
from src.domain.ports.image_store_port import ImageStorePort
from src.domain.ports.vision_ai_service_port import VisionAIServicePort
from src.domain.services.food_mapping_service import FoodMappingService
from src.infra.adapters.cloudflare_workers_image_generator import (
    CloudflareWorkersImageGenerator,
)
from src.infra.adapters.cloudinary_image_store import CloudinaryImageStore
from src.infra.adapters.food_cache_service import FoodCacheService
from src.infra.adapters.food_data_service import FoodDataService
from src.infra.adapters.open_food_facts_service import (
    get_open_food_facts_service,
)
from src.infra.adapters.vision_ai_service import VisionAIService
from src.infra.cache.cache_service import CacheService
from src.infra.cache.metrics import CacheMonitor
from src.infra.cache.redis_client import RedisClient
from src.infra.config.settings import settings
from src.infra.database.config_async import get_async_db
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.repositories.admin_meal_catalog_repository_async import (
    AsyncAdminMealCatalogRepository,
)
from src.infra.repositories.catalog_recipe_repository_async import (
    AsyncCatalogMealRepository,
)
from src.infra.repositories.food_reference_repository_async import (
    AsyncFoodReferenceRepository,
)

if TYPE_CHECKING:
    from src.domain.ports.subscription_service_port import SubscriptionServicePort

# Note: Old handler imports removed - using event-driven architecture now
# from src.app.handlers.activity_handler import ActivityHandler
# ... etc


# Globals
logger = logging.getLogger(__name__)
_redis_client: RedisClient | None = None
_cache_service: CacheService | None = None
_cache_monitor = CacheMonitor()

# Singleton service instances (initialized once, reused across requests)
_image_store: ImageStorePort | None = None
_vision_service: VisionAIServicePort | None = None
_catalog_meal_snapshot_service: CatalogMealSnapshotService | None = None
_catalog_meal_browse_service = None


async def initialize_cache_layer() -> None:
    """Initialize Redis for optional caches and the provider budget."""
    global _redis_client, _cache_service

    if not settings.CACHE_ENABLED and settings.NUTRITION_PROVIDER_GLOBAL_RPM is None:
        logger.info("Caching disabled via settings")
        return

    if not settings.CACHE_ENABLED:
        logger.info(
            "Optional caching disabled; Redis remains enabled for provider budget"
        )

    if _redis_client is None:
        _redis_client = RedisClient(
            redis_url=settings.redis_url,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
        )
        await _redis_client.connect()

    _cache_service = CacheService(
        redis_client=_redis_client,
        default_ttl=settings.CACHE_DEFAULT_TTL,
        monitor=_cache_monitor,
        enabled=settings.CACHE_ENABLED,
    )


async def shutdown_cache_layer() -> None:
    """Gracefully close Redis connections."""
    global _redis_client, _cache_service

    if _cache_service:
        _cache_service = None
    if _redis_client:
        await _redis_client.disconnect()
        _redis_client = None


# Database
async def get_db():
    """Backward-compatible FastAPI dependency that yields an async DB session."""
    async for session in get_async_db():
        yield session


# Image Store (singleton pattern)
def get_image_store() -> ImageStorePort:
    """
    Get the image store adapter instance (singleton).

    Returns:
        ImageStorePort: The image store adapter (Cloudinary or Mock)
    """
    global _image_store
    if _image_store is None:
        _image_store = CloudinaryImageStore()
    return _image_store


# Vision Service (singleton pattern)
def get_vision_service() -> VisionAIServicePort:
    """
    Get the vision AI service instance (singleton).

    Returns:
        VisionAIServicePort: The vision service
    """
    global _vision_service
    if _vision_service is None:
        _vision_service = VisionAIService()
    return _vision_service


# Vision Response Parser
def get_vision_parser() -> VisionResponseParser:
    """
    Get the vision response parser instance.

    Returns:
        VisionResponseParser: The parser instance
    """
    return VisionResponseParser(strict_schema_mode=True)


# Backward-compat alias so existing code referring to get_gpt_parser continues to work.
get_gpt_parser = get_vision_parser


# Food Cache Service
def get_food_cache_service() -> FoodCacheServicePort:
    """
    Get the food cache service instance.

    Returns:
        FoodCacheServicePort: The food cache service
    """
    return FoodCacheService(cache_service=_cache_service)


def get_cache_service() -> CacheService | None:
    """Expose cache service for dependency injection."""
    return _cache_service


def get_cache_monitor() -> CacheMonitor:
    """Return shared cache monitor for metrics."""
    return _cache_monitor


def get_admin_meal_catalog_repository(
    db: AsyncSession = Depends(get_async_db),
) -> AsyncAdminMealCatalogRepository:
    """Return admin catalog repository bound to the request session."""

    return AsyncAdminMealCatalogRepository(db)


def get_catalog_meal_seed_importer(
    db: AsyncSession = Depends(get_async_db),
) -> CatalogMealSeedImporter:
    """Build the catalog seed importer with request-scoped repositories."""

    food_reference_repository = AsyncFoodReferenceRepository(db)

    async def enrich_missing_with_fatsecret(name: str) -> bool:
        from src.domain.services.meal_suggestion.ingredient_nutrition_resolver import (
            IngredientNutritionResolver,
        )

        resolver = IngredientNutritionResolver(
            fatsecret=get_fat_secret_service_instance(),
            food_ref_repo=food_reference_repository,
        )
        return await resolver.resolve(name) is not None

    return CatalogMealSeedImporter(
        AsyncCatalogMealRepository(db),
        food_reference_repository,
        candidate_enricher=enrich_missing_with_fatsecret,
    )


def get_catalog_meal_snapshot_service() -> CatalogMealSnapshotService:
    """Return the process-local snapshot shared by catalog readers."""
    global _catalog_meal_snapshot_service
    if _catalog_meal_snapshot_service is None:
        _catalog_meal_snapshot_service = CatalogMealSnapshotService()
    return _catalog_meal_snapshot_service


def get_catalog_meal_browse_service():
    """Return the read-only public catalog browser service."""
    global _catalog_meal_browse_service
    if _catalog_meal_browse_service is None:
        from src.app.services.catalog_meal_browse_service import (
            CatalogMealBrowseService,
        )
        from src.app.services.meal_recommendation_history_projector import (
            MealRecommendationHistoryProjector,
        )

        _catalog_meal_browse_service = CatalogMealBrowseService(
            uow_factory=AsyncUnitOfWork,
            snapshot_service=get_catalog_meal_snapshot_service(),
            history_projector=MealRecommendationHistoryProjector(),
        )
    return _catalog_meal_browse_service


def get_catalog_food_reference_review_service(
    db: AsyncSession = Depends(get_async_db),
) -> CatalogFoodReferenceReviewService:
    """Build the request-scoped service that records admin reference approvals."""

    return CatalogFoodReferenceReviewService(AsyncFoodReferenceRepository(db))


def get_catalog_image_generator() -> CloudflareWorkersImageGenerator:
    """Return catalog image generator configured with Cloudflare and Cloudinary."""

    try:
        return CloudflareWorkersImageGenerator(
            account_id=settings.CLOUDFLARE_ACCOUNT_ID,
            api_token=settings.CLOUDFLARE_API_TOKEN,
            model=settings.CLOUDFLARE_WORKERS_AI_IMAGE_MODEL,
            timeout=settings.CLOUDFLARE_WORKERS_AI_TIMEOUT_SECONDS,
            image_store=CloudinaryImageStore(),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


def get_catalog_image_generator_factory() -> Callable[
    [], CloudflareWorkersImageGenerator
]:
    """Return a lazy generator factory so routes can finish cheap prechecks first."""

    return get_catalog_image_generator


# Food Mapping Service
def get_food_mapping_service() -> FoodMappingServicePort:
    """
    Get the food mapping service instance.

    Returns:
        FoodMappingServicePort: The food mapping service
    """
    return FoodMappingService()


# Food Data Service (USDA FoodData Central)
def get_food_data_service() -> FoodDataService:
    """
    Get the food data service instance for USDA FoodData Central API.

    Returns:
        FoodDataService: The food data service
    """
    return FoodDataService()


# OpenFoodFacts Service
def get_open_food_facts_service_instance():
    """Get the OpenFoodFacts service instance."""
    return get_open_food_facts_service()


# fatsecret Service
def get_fat_secret_service_instance():
    """Get the fatsecret service instance."""
    from src.infra.adapters.fat_secret_service import get_fat_secret_service

    return get_fat_secret_service()


def get_meal_analyze_graph_settings():
    """Return meal-analysis graph settings needed by API composition."""
    from src.infra.config.settings import get_settings

    current_settings = get_settings()
    return {
        "external_provider_timeout_seconds": (
            current_settings.AI_MEAL_ANALYZE_EXTERNAL_PROVIDER_TIMEOUT_SECONDS
        ),
        "fatsecret_validation_enabled": (
            current_settings.AI_MEAL_ANALYZE_FATSECRET_VALIDATION_ENABLED
        ),
        "graph_enabled": current_settings.AI_MEAL_ANALYZE_GRAPH_ENABLED,
        "graph_version": current_settings.AI_MEAL_ANALYZE_GRAPH_VERSION,
    }


def get_parse_text_settings() -> dict[str, bool]:
    """Return parse-text feature settings for API composition."""
    from src.infra.config.settings import get_settings

    current_settings = get_settings()
    return {
        "structured_reference_enabled": bool(
            getattr(current_settings, "PARSE_TEXT_STRUCTURED_REFERENCE_ENABLED", False)
        )
    }


def get_meal_recommendation_analytics_service() -> MealRecommendationAnalyticsService:
    """Return privacy-safe recommendation analytics."""
    posthog_module = import_module("src.infra.adapters.posthog_adapter")
    return MealRecommendationAnalyticsService(
        salt=settings.MEAL_RECOMMENDATIONS_ANALYTICS_SALT,
        adapter=posthog_module.PostHogAdapter(),
    )


# Food Reference Repository (replaces barcode_product_repository)
_async_food_reference_repository = None


def get_async_food_reference_repository():
    """Get the async food reference repository adapter singleton."""
    global _async_food_reference_repository
    if _async_food_reference_repository is None:
        from src.infra.repositories.food_reference_uow_adapter import (
            AsyncFoodReferenceUowAdapter,
        )

        _async_food_reference_repository = AsyncFoodReferenceUowAdapter()
    return _async_food_reference_repository


# Backward-compatible aliases for older callers; runtime receives async adapter.
get_food_reference_repository = get_async_food_reference_repository
get_barcode_product_repository = get_food_reference_repository



# Phase 06: Meal Suggestion Dependencies
def get_raw_redis_client():
    """Return the underlying async Redis client (None if not initialized)."""
    return _redis_client.client if _redis_client and _redis_client.client else None


def get_meal_suggestion_repository():
    """Get Redis-backed meal suggestion session store."""
    from src.infra.repositories.meal_suggestion_repository import (
        MealSuggestionRepository,
    )

    if _redis_client is None:
        raise RuntimeError(
            "Redis suggestion session store not initialized. "
            "Meal suggestion sessions require Redis until moved to durable storage."
        )
    return MealSuggestionRepository(_redis_client)


_suggestion_translation_service = None


def get_suggestion_translation_service():
    """Get the persisted suggestion translation service (singleton)."""
    global _suggestion_translation_service

    if _suggestion_translation_service is not None:
        return _suggestion_translation_service

    # Requires text translation service
    text_service = get_text_translation_service()
    if text_service is None:
        logger.warning("OPENAI_API_KEY not set - suggestion translation will be skipped")
        return None

    from src.domain.services.meal_suggestion.suggestion_translation_service import (
        SuggestionTranslationService,
    )

    _suggestion_translation_service = SuggestionTranslationService(
        text_translation_service=text_service
    )
    logger.info("OpenAI suggestion translation service initialised")
    return _suggestion_translation_service


def get_suggestion_orchestration_service():
    """
    Get suggestion orchestration service (singleton-safe).

    This service uses AsyncUnitOfWork for DB-backed user profile lookups.
    """
    from src.domain.services.meal_suggestion.suggestion_orchestration_service import (
        SuggestionOrchestrationService,
    )
    from src.infra.adapters.meal_generation_service import MealGenerationService
    from src.infra.database.uow_async import AsyncUnitOfWork
    from src.infra.services.ai.schemas import (
        DiscoveryMealsResponse,
        MealNamesResponse,
        RecipeDetailsResponse,
    )

    meal_gen_service = MealGenerationService()
    suggestion_repo = get_meal_suggestion_repository()

    async def profile_provider(user_id: str):
        async with AsyncUnitOfWork() as uow:
            return await uow.users.get_profile(UUID(user_id))

    return SuggestionOrchestrationService(
        generation_service=meal_gen_service,
        suggestion_repo=suggestion_repo,
        nutrition_lookup=get_nutrition_lookup_service(),
        profile_provider=profile_provider,
        uow_factory=AsyncUnitOfWork,
        meal_names_schema_class=MealNamesResponse,
        discovery_meals_schema_class=DiscoveryMealsResponse,
        recipe_details_schema_class=RecipeDetailsResponse,
        translation_service=get_suggestion_translation_service(),
    )


# Note: Old handler functions removed - using event-driven architecture now
# The event bus configuration in event_bus.py handles all dependencies


_meal_translation_service = None
_async_meal_translation_repository = None


def get_async_meal_translation_repository():
    """Get the async meal translation repository adapter singleton."""
    global _async_meal_translation_repository
    if _async_meal_translation_repository is None:
        from src.infra.repositories.meal_translation_uow_adapter import (
            AsyncMealTranslationUowAdapter,
        )

        _async_meal_translation_repository = AsyncMealTranslationUowAdapter()
    return _async_meal_translation_repository


def get_meal_translation_service():
    """Get the persisted meal translation service (singleton)."""
    global _meal_translation_service

    if _meal_translation_service is not None:
        return _meal_translation_service

    # Requires text translation service
    text_service = get_text_translation_service()
    if text_service is None:
        logger.warning("OPENAI_API_KEY not set - meal translation will be skipped")
        return None

    from src.domain.services.meal_analysis.meal_translation_service import (
        MealTranslationService,
    )

    _meal_translation_service = MealTranslationService(
        translation_repo=get_async_meal_translation_repository(),
        text_translation_service=text_service,
    )
    logger.info("OpenAI meal translation service initialised")
    return _meal_translation_service


_text_translation_service = None


def get_text_translation_service():
    """Get the neutral OpenAI-backed translation service for read paths.

    Translation is optional: a missing OpenAI key leaves callers with their
    canonical response and must never prevent event-bus startup.  The
    provider and adapter are process-scoped so requests do not construct SDK
    clients repeatedly.
    """

    global _text_translation_service
    if _text_translation_service is not None:
        return _text_translation_service
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set - translation will be skipped")
        return None

    from src.domain.services.translation.text_translation_service import (
        TextTranslationService,
    )

    adapter_module = import_module("src.infra.adapters.openai_translation_adapter")
    provider_module = import_module(
        "src.infra.services.ai.providers.openai_provider"
    )

    provider = provider_module.OpenAIProvider(
        api_key=settings.OPENAI_API_KEY,
        request_timeout_seconds=max(1, int(settings.OPENAI_TRANSLATION_TIMEOUT_SECONDS)),
        max_retries=settings.OPENAI_MAX_RETRIES,
        store_responses=False,
        prompt_cache_enabled=settings.OPENAI_PROMPT_CACHE_ENABLED,
        prompt_cache_retention=settings.OPENAI_PROMPT_CACHE_RETENTION,
        prompt_cache_key_prefix=settings.OPENAI_PROMPT_CACHE_KEY_PREFIX,
    )
    _text_translation_service = TextTranslationService(
        adapter_module.OpenAITranslationAdapter(
            provider=provider,
            model=settings.OPENAI_TRANSLATION_MODEL,
            timeout_seconds=settings.OPENAI_TRANSLATION_TIMEOUT_SECONDS,
        )
    )
    logger.info("OpenAI translation service initialised")
    return _text_translation_service


def get_serving_label_dependencies():
    """Compose serving-label enrichment dependencies for read routes."""
    from src.app.services.meal_serving_label_enricher import (
        enrich_meal_serving_labels,
    )

    return enrich_meal_serving_labels, get_text_translation_service(), AsyncUnitOfWork


def get_ai_model_manager():
    """Get provider-agnostic AI manager singleton."""
    from src.infra.services.ai.ai_model_manager import AIModelManager

    return AIModelManager.get_instance()


# IngredientNutritionResolver singleton reuses fatsecret and food references.
_ingredient_nutrition_resolver = None


def get_ingredient_nutrition_resolver():
    """Get IngredientNutritionResolver singleton."""
    global _ingredient_nutrition_resolver
    if _ingredient_nutrition_resolver is None:
        from src.domain.services.meal_suggestion.ingredient_nutrition_resolver import (
            IngredientNutritionResolver,
        )

        _ingredient_nutrition_resolver = IngredientNutritionResolver(
            fatsecret=get_fat_secret_service_instance(),
            food_ref_repo=get_async_food_reference_repository(),
        )
    return _ingredient_nutrition_resolver


# NutritionLookupService singleton uses food refs, resolver, and generation.
_nutrition_lookup_service = None


def get_nutrition_lookup_service():
    """Get NutritionLookupService singleton."""
    global _nutrition_lookup_service
    if _nutrition_lookup_service is None:
        from src.domain.services.meal_suggestion.nutrition_lookup_service import (
            NutritionLookupService,
        )
        from src.infra.adapters.meal_generation_service import MealGenerationService

        _nutrition_lookup_service = NutritionLookupService(
            food_ref_repo=get_async_food_reference_repository(),
            ingredient_nutrition_resolver=get_ingredient_nutrition_resolver(),
            generation_service=MealGenerationService(),
            redis_client=_redis_client,
            cache_service=get_cache_service(),
        )
    return _nutrition_lookup_service


# Singleton subscription service instance
_subscription_service: Optional["SubscriptionServicePort"] = None


def get_subscription_service() -> "SubscriptionServicePort":
    """
    Get the subscription service instance.

    Returns:
        SubscriptionServicePort: The subscription service
    """
    global _subscription_service

    if _subscription_service is None:
        from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter

        api_key = os.getenv("REVENUECAT_SECRET_API_KEY", "")
        _subscription_service = RevenueCatAdapter(api_key=api_key)

    return _subscription_service


def get_web_funnel_redemption_service():
    """Build the infrastructure redemption service at the composition boundary."""
    from src.bootstrap.web_funnel_redemption import (
        get_web_funnel_redemption_service as factory,
    )

    return factory()
