"""
Main application file for the MealTrack API.

This file initializes the FastAPI application, sets up middleware,
includes routes, and handles application lifecycle events.
"""

# Prefer IPv4 to avoid hangs when IPv6 is unreachable (common in local dev)
import socket

_original_getaddrinfo = socket.getaddrinfo


def _ipv4_first_getaddrinfo(*args, **kwargs):
    results = _original_getaddrinfo(*args, **kwargs)
    results.sort(key=lambda x: x[0] != socket.AF_INET)
    return results


socket.getaddrinfo = _ipv4_first_getaddrinfo

import json
import logging
import os
from contextlib import asynccontextmanager

import firebase_admin
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from firebase_admin import credentials

from src.api.base_dependencies import (
    initialize_cache_layer,
    shutdown_cache_layer,
)
from src.api.exception_handlers import register_exception_handlers
from src.api.middleware.accept_language import AcceptLanguageMiddleware
from src.api.middleware.dev_auth_bypass import add_dev_auth_bypass
from src.api.middleware.preview_body_limit import PreviewBodyLimitMiddleware
from src.api.middleware.rate_limit import limiter
from src.api.middleware.request_logger import RequestLoggerMiddleware
from src.api.routes.app_download import router as app_download_router
from src.api.routes.v1.activities import router as activities_router
from src.api.routes.v1.admin_meal_catalog import router as admin_meal_catalog_router
from src.api.routes.v1.admin_meal_catalog_import import (
    router as admin_meal_catalog_import_router,
)
from src.api.routes.v1.capabilities import router as capabilities_router
from src.api.routes.v1.cheat_days import router as cheat_days_router
from src.api.routes.v1.codes import router as codes_router
from src.api.routes.v1.feature_flags import router as feature_flags_router
from src.api.routes.v1.foods import router as foods_router
from src.api.routes.v1.health import root_router as root_health_router
from src.api.routes.v1.health import router as health_router
from src.api.routes.v1.hydration import router as hydration_router
from src.api.routes.v1.ingredients import router as ingredients_router
from src.api.routes.v1.meal_catalog import router as meal_catalog_router
from src.api.routes.v1.meal_catalog_log import router as meal_catalog_log_router
from src.api.routes.v1.meal_recommendations import router as meal_recommendations_router
from src.api.routes.v1.meal_scan_by_url import router as meal_scan_by_url_router
from src.api.routes.v1.meal_suggestions import router as meal_suggestions_router
from src.api.routes.v1.meals import router as meals_router
from src.api.routes.v1.monitoring import router as monitoring_router
from src.api.routes.v1.movement import router as movement_router
from src.api.routes.v1.notifications import router as notifications_router
from src.api.routes.v1.nutrition import router as nutrition_router
from src.api.routes.v1.progress import router as progress_router
from src.api.routes.v1.promo_codes import router as promo_codes_router
from src.api.routes.v1.referrals import router as referrals_router
from src.api.routes.v1.saved_suggestions import router as saved_suggestions_router
from src.api.routes.v1.tdee import router as tdee_router
from src.api.routes.v1.user_profiles import router as user_profiles_router
from src.api.routes.v1.users import router as users_router
from src.api.routes.v1.web_funnel import router as web_funnel_router
from src.api.routes.v1.webhooks import router as webhooks_router
from src.api.routes.v1.weight_entries import router as weight_entries_router
from src.api.routes.well_known import router as well_known_router
from src.bootstrap.observability import initialize_observability
from src.infra.config.settings import settings
from src.infra.database.config_async import async_engine

load_dotenv()

# Initialize observability before creating the FastAPI app so provider integrations
# can patch framework middleware.
initialize_observability()

# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO))
logger = logging.getLogger(__name__)


async def warm_database_connection() -> None:
    """Warm the async database connection so cold Neon compute wakes before traffic."""
    if async_engine is None:
        raise RuntimeError("Async database engine is not initialized")

    from sqlalchemy import text

    async with async_engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        await conn.commit()


def initialize_firebase():
    """
    Initialize Firebase Admin SDK.

    Supports three methods (in priority order):
    1. Service account JSON file path via FIREBASE_CREDENTIALS (recommended for local development)
    2. Service account JSON string via FIREBASE_SERVICE_ACCOUNT_JSON (recommended for production)
    3. Default credentials (fallback for cloud environments)

    Environment Variables:
    - FIREBASE_CREDENTIALS: Path to service account JSON file
    - FIREBASE_SERVICE_ACCOUNT_JSON: Service account JSON as string
    """
    try:
        # Check if already initialized
        firebase_admin.get_app()
        logger.info("Firebase already initialized")
        return
    except ValueError:
        # Not initialized yet, proceed with initialization
        pass

    try:
        environment = os.getenv("ENVIRONMENT", "development")

        # Option 1: Check for service account file path
        credentials_path = os.getenv("FIREBASE_CREDENTIALS")
        if credentials_path and os.path.exists(credentials_path):
            cred = credentials.Certificate(credentials_path)
            firebase_admin.initialize_app(cred)
            logger.info(
                "Firebase initialized with service account file (environment: %s)",
                environment,
            )
            return

        # Option 2: Check for service account JSON string
        service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
        if service_account_json:
            try:
                service_account_dict = json.loads(service_account_json)
                cred = credentials.Certificate(service_account_dict)
                firebase_admin.initialize_app(cred)
                logger.info(
                    "Firebase initialized with service account JSON string (environment: %s)",
                    environment,
                )
                return
            except json.JSONDecodeError as e:
                logger.error("Invalid JSON in FIREBASE_SERVICE_ACCOUNT_JSON: %s", e)
                raise ValueError(
                    "FIREBASE_SERVICE_ACCOUNT_JSON contains invalid JSON"
                ) from e

        # Option 3: Fall back to default credentials
        firebase_admin.initialize_app()
        logger.info(
            "Firebase initialized with default credentials (environment: %s)",
            environment,
        )

    except Exception as e:
        logger.error("Failed to initialize Firebase: %s", e)
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown events."""
    # Startup
    logger.info("Starting MealTrack API...")

    # PostHog LLM Analytics via OpenTelemetry — must run before any LangChain calls
    _posthog_key = os.getenv("POSTHOG_API_KEY")
    if _posthog_key:
        try:
            from opentelemetry import trace
            from opentelemetry.instrumentation.langchain import LangchainInstrumentor
            from opentelemetry.sdk.resources import SERVICE_NAME, Resource
            from opentelemetry.sdk.trace import TracerProvider
            from posthog.ai.otel import PostHogSpanProcessor

            _otel_provider = TracerProvider(
                resource=Resource(attributes={SERVICE_NAME: "mealtrack-backend"})
            )
            _otel_provider.add_span_processor(
                PostHogSpanProcessor(
                    api_key=_posthog_key,
                    host=os.getenv("POSTHOG_HOST", "https://us.i.posthog.com"),
                )
            )
            trace.set_tracer_provider(_otel_provider)
            LangchainInstrumentor().instrument()
            logger.info("PostHog LLM Analytics instrumented via OpenTelemetry")
        except Exception as e:
            logger.warning(f"PostHog LLM Analytics init failed (non-fatal): {e}")
    else:
        logger.info("POSTHOG_API_KEY not set — LLM analytics disabled")

    # Initialize Firebase Admin SDK
    try:
        initialize_firebase()
    except Exception as e:
        logger.critical("Failed to initialize Firebase; aborting startup: %s", e)
        raise

    # NOTE: Database migrations are run via docker-entrypoint.sh BEFORE app startup
    # This ensures migrations complete before any workers start, preventing race conditions
    # See: migrations/run.py and docker-entrypoint.sh

    # Warm database connection — triggers Neon compute wakeup on cold start
    try:
        await warm_database_connection()
        logger.info("Database connection warmed successfully")
    except Exception as e:
        logger.warning("Database connection warming failed: %s", e)

    # Initialize Redis cache (must happen BEFORE notification service)
    try:
        await initialize_cache_layer()
    except Exception as exc:
        logger.error("Failed to initialize cache layer: %s", exc)
        if os.getenv("FAIL_ON_CACHE_ERROR", "false").lower() == "true":
            logger.critical("Cache layer is required; aborting startup")
            raise

    # Eagerly build singleton event buses during single-threaded startup so
    # concurrent first requests never race the lazy initializer (which would
    # otherwise build several throwaway buses on a cold start).
    try:
        from src.api.dependencies.event_bus import (
            get_configured_event_bus,
            get_food_search_event_bus,
        )

        get_configured_event_bus()
        get_food_search_event_bus()
        logger.info("Event buses initialized")
    except Exception as e:
        logger.warning("Event bus eager init failed (non-fatal): %s", e)

    logger.info("MealTrack API started successfully!")
    yield

    # Shutdown
    logger.info("Shutting down MealTrack API...")

    # Disconnect cache
    await shutdown_cache_layer()

    # Dispose async SQLAlchemy engine so pooled asyncpg connections are returned
    if async_engine is not None:
        await async_engine.dispose()


app = FastAPI(
    title="MealTrack API",
    description="API for meal tracking, nutritional analysis with AI vision capabilities, and smart meal planning",
    version="0.2.0",
    lifespan=lifespan,
)

# Bound unauthenticated preview bodies before FastAPI buffers or decodes JSON.
# Register this first so CORS and observability middleware still see its 413 response.
app.add_middleware(PreviewBodyLimitMiddleware)

allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Request/Response logging
app.add_middleware(RequestLoggerMiddleware)

# Accept-Language header parsing
app.add_middleware(AcceptLanguageMiddleware)

# Rate limiting
from slowapi.errors import RateLimitExceeded

from src.api.middleware.rate_limit import rate_limit_exceeded_handler

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Global exception boundary — one authoritative ERROR per unexpected failure
register_exception_handlers(app)

# Dev auth bypass: inject a fixed user during development
add_dev_auth_bypass(app)

# Include all routers
app.include_router(root_health_router)
app.include_router(health_router)
app.include_router(admin_meal_catalog_router)
app.include_router(admin_meal_catalog_import_router)
app.include_router(meals_router)
app.include_router(meal_scan_by_url_router)
app.include_router(activities_router)
app.include_router(feature_flags_router)
app.include_router(capabilities_router)
app.include_router(meal_suggestions_router)
app.include_router(meal_recommendations_router)
app.include_router(meal_catalog_log_router)
app.include_router(meal_catalog_router)
app.include_router(user_profiles_router)
app.include_router(users_router)
app.include_router(foods_router)
app.include_router(monitoring_router)
app.include_router(webhooks_router)
app.include_router(web_funnel_router)
app.include_router(notifications_router)
app.include_router(hydration_router)
app.include_router(ingredients_router)
app.include_router(tdee_router)
app.include_router(saved_suggestions_router)
app.include_router(cheat_days_router)
app.include_router(capabilities_router)
app.include_router(referrals_router)
app.include_router(promo_codes_router)
app.include_router(codes_router)
app.include_router(nutrition_router)
app.include_router(progress_router)
app.include_router(weight_entries_router)
app.include_router(movement_router)
app.include_router(well_known_router)
app.include_router(app_download_router)

# Serve static files from uploads directory (development)
if os.environ.get("ENVIRONMENT") == "development":
    uploads_path = os.getenv(
        "UPLOADS_DIR", "./uploads"
    )  # Default to './uploads' if UPLOADS_DIR is not set
    if os.path.exists(uploads_path):
        app.mount("/uploads", StaticFiles(directory=uploads_path), name="uploads")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
