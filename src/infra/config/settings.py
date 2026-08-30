"""
Application configuration settings loaded from environment variables.
"""

import json
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.domain.services.meal_analysis.fast_path_policy import (
    MEAL_ANALYZE_DEFAULT_MAX_OUTPUT_TOKENS,
)


class Settings(BaseSettings):
    """Centralized application settings."""

    # General application settings
    ENVIRONMENT: str = Field(default="development")
    FAIL_ON_MIGRATION_ERROR: bool = Field(default=False)
    FAIL_ON_CACHE_ERROR: bool = Field(default=False)
    AUTO_MIGRATE: bool = Field(default=True)
    MIGRATION_TIMEOUT: int = Field(default=60)
    MIGRATION_RETRY_ATTEMPTS: int = Field(default=3)
    MIGRATION_RETRY_DELAY: float = Field(default=2.0)

    # Admin authorization — comma-separated emails allowed to call privileged
    # endpoints (e.g. feature-flag mutations). Empty means no admins.
    ADMIN_EMAILS: str = Field(default="")

    # Shared service token for infra monitoring endpoints (health pool/connection
    # stats, cache metrics) — scrapers send it as the X-Monitoring-Token header.
    # Empty = those endpoints are closed to everyone (fail-closed).
    MONITORING_API_TOKEN: str = Field(default="")

    # Database configuration
    DATABASE_URL: str | None = Field(default=None)
    APP_DATABASE_URL: str | None = Field(
        default=None,
        description="App runtime DB URL. Prefer direct Neon URL in production.",
    )
    DATABASE_URL_DIRECT: str | None = Field(
        default=None,
        description="Direct Neon URL for Alembic migrations only. Not used by app runtime.",
    )
    DB_CONNECTION_MODE: str = Field(
        default="direct_pool",
        description="DB connection mode: 'direct_pool' (AsyncAdaptedQueuePool) or 'neon_pooler' (NullPool)",
    )
    ASYNC_POOL_SIZE_PER_WORKER: int | None = Field(
        default=None,
        description="Async pool connections per worker (overrides POOL_SIZE_PER_WORKER)",
    )
    ASYNC_POOL_MAX_OVERFLOW: int | None = Field(
        default=None,
        description="Async pool max overflow (overrides POOL_MAX_OVERFLOW)",
    )
    ASYNC_POOL_TIMEOUT: int | None = Field(
        default=None,
        description="Async pool connection timeout seconds (overrides POOL_TIMEOUT)",
    )
    ASYNC_POOL_RECYCLE: int = Field(
        default=120,
        description="Async pool connection recycle interval seconds",
    )
    DB_USER: str = Field(default="nutree")
    DB_PASSWORD: str = Field(default="")
    DB_HOST: str = Field(default="localhost")
    DB_PORT: int = Field(default=5432)
    DB_NAME: str = Field(default="nutree")

    # Connection pool tuning
    UVICORN_WORKERS: int = Field(default=4, description="Number of worker processes")
    POOL_SIZE_PER_WORKER: int = Field(
        default=3, description="DB connections per worker"
    )
    POOL_MAX_OVERFLOW: int = Field(default=2, description="Max overflow connections")
    POOL_TIMEOUT: int = Field(default=10)
    POOL_RECYCLE: int = Field(default=120)
    POOL_ECHO: bool = Field(default=False)

    # Redis configuration (optimized for low-memory)
    # Prefer setting REDIS_URL directly for hosted providers (e.g., Upstash)
    REDIS_URL: str | None = Field(default=None)
    REDIS_HOST: str = Field(default="localhost")
    REDIS_PORT: int = Field(default=6379)
    REDIS_DB: int = Field(default=0)
    REDIS_USERNAME: str | None = Field(default=None)
    REDIS_PASSWORD: str | None = Field(default=None)
    REDIS_SSL: bool = Field(default=False)
    REDIS_MAX_CONNECTIONS: int = Field(
        default=10, description="Max Redis connections (reduced from 50)"
    )

    # Cache configuration
    CACHE_ENABLED: bool = Field(default=True)
    CACHE_DEFAULT_TTL: int = Field(default=3600)  # 1 hour

    # Cloudflare Queue integration is required for asynchronous side effects.
    CLOUDFLARE_QUEUE_ACCOUNT_ID: str = Field(default="")
    CLOUDFLARE_QUEUE_ID: str = Field(default="")
    # Worker/Wrangler configuration uses the human-readable queue name. The
    # backend REST publisher uses CLOUDFLARE_QUEUE_ID instead.
    CLOUDFLARE_QUEUE_NAME: str = Field(default="")
    CLOUDFLARE_QUEUE_API_TOKEN: str = Field(default="")
    CLOUDFLARE_QUEUE_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0)

    # Firebase
    FIREBASE_CREDENTIALS: str | None = Field(default=None)
    FIREBASE_SERVICE_ACCOUNT_JSON: str | None = Field(default=None)
    FIREBASE_SERVICE_ACCOUNT_PATH: str | None = Field(default=None)

    # Email (Resend)
    RESEND_API_KEY: str | None = Field(default=None)
    EMAIL_FROM: str = Field(default="Nutree <hello@nutree.app>")
    EMAIL_ENABLED: bool = Field(default=False)

    # External APIs & integrations
    USDA_FDC_API_KEY: str | None = Field(default=None)
    FATSECRET_CLIENT_ID: str | None = Field(
        default=None, description="fatsecret OAuth 2.0 client ID"
    )
    FATSECRET_CLIENT_SECRET: str | None = Field(
        default=None, description="fatsecret OAuth 2.0 client secret"
    )
    NUTRITION_PROVIDER_GLOBAL_RPM: int = Field(
        default=60,
        ge=1,
        description="Shared per-minute FatSecret budget for provider-origin meal saves.",
    )
    BRAVE_SEARCH_API_KEY: str | None = Field(
        default=None, description="Brave Search API key (free tier: 2K/mo)"
    )

    # LLM Provider configuration
    AI_PRIMARY_PROVIDER: str = Field(
        default="openai",
        description="Primary AI provider.",
    )
    AI_FALLBACK_PROVIDER: str = Field(
        default="cloudflare-workers-ai",
        description="Fallback AI provider after the primary provider fails transiently.",
    )
    OPENAI_API_KEY: str | None = Field(default=None)
    OPENAI_VISION_MODEL: str = Field(default="gpt-5.4-mini-2026-03-17")
    OPENAI_TEXT_MODEL: str = Field(default="gpt-5.4-mini-2026-03-17")
    OPENAI_PARSE_TEXT_MODEL: str = Field(
        default="gpt-5.6-luna-2026-06-01",
        description="Primary OpenAI Luna model for parse-text.",
    )
    PARSE_TEXT_PURE_AI_ENABLED: bool = Field(
        default=True,
        description="Enable pure AI parsing for meal text without blocking on external provider lookups.",
    )
    OPENAI_TRANSLATION_MODEL: str = Field(default="gpt-5.4-mini-2026-03-17")
    OPENAI_TRANSLATION_TIMEOUT_SECONDS: float = Field(default=8.0)
    OPENAI_REQUEST_TIMEOUT_SECONDS: int = Field(default=20)
    OPENAI_MAX_RETRIES: int = Field(default=1)
    OPENAI_STORE_RESPONSES: bool = Field(
        default=False,
        description="Whether OpenAI may store Responses API payloads.",
    )
    OPENAI_PROMPT_CACHE_ENABLED: bool = Field(
        default=True,
        description="Enable explicit OpenAI Responses API prompt cache routing.",
    )
    OPENAI_PROMPT_CACHE_RETENTION: str | None = Field(
        default="",
        description="Optional OpenAI prompt_cache_retention: empty, in_memory, or 24h.",
    )
    OPENAI_PROMPT_CACHE_KEY_PREFIX: str = Field(
        default="mealtrack",
        description="Safe prefix for OpenAI prompt_cache_key values.",
    )
    # Image search (meal discovery photos)
    PEXELS_API_KEY: str | None = Field(
        default=None, description="Pexels API key for food photos"
    )
    UNSPLASH_ACCESS_KEY: str | None = Field(
        default=None, description="Unsplash Client-ID access key"
    )
    REVENUECAT_SECRET_API_KEY: str | None = Field(default=None)
    REVENUECAT_WEBHOOK_SECRET: str | None = Field(default=None)
    WEB_FUNNEL_REVENUECAT_ENVIRONMENT: str = Field(default="")
    WEB_FUNNEL_REDEMPTION_ENABLED: bool = Field(default=False)
    WEB_FUNNEL_CHECKOUT_ADMISSION_ENABLED: bool = Field(default=True)
    WEB_FUNNEL_LEGACY_CLAIM_ENABLED: bool = Field(default=False)
    WEB_FUNNEL_CLAIM_LINK_BASE_URL: str = Field(default="")
    WEB_FUNNEL_BFF_ORIGIN: str = Field(default="")
    WEB_FUNNEL_BFF_SHARED_SECRET: str = Field(default="")
    CLOUDINARY_CLOUD_NAME: str | None = Field(default=None)
    CLOUDINARY_API_KEY: str | None = Field(default=None)
    CLOUDINARY_API_SECRET: str | None = Field(default=None)

    # CORS
    ALLOWED_ORIGINS: str = Field(
        default="", description="Comma-separated list of allowed CORS origins"
    )

    # Sentry error monitoring
    SENTRY_DSN: str | None = Field(
        default=None, description="Sentry DSN; disables Sentry when unset"
    )
    SENTRY_RELEASE: str | None = Field(
        default=None, description="Application release identifier for Sentry"
    )
    SENTRY_TRACES_SAMPLE_RATE: float = Field(
        default=0.1, description="Performance trace sample rate (0.0-1.0)"
    )
    SENTRY_PROFILES_SAMPLE_RATE: float = Field(
        default=0.05, description="Profile sample rate (0.0-1.0)"
    )
    SENTRY_ENABLE_LOGS: bool = Field(
        default=True, description="Enable Sentry Logs ingestion"
    )
    SENTRY_ENABLE_METRICS: bool = Field(
        default=True, description="Enable Sentry application metrics ingestion"
    )
    SENTRY_PROFILE_SESSION_SAMPLE_RATE: float | None = Field(
        default=None,
        description="Optional Sentry profile session sample rate (0.0-1.0)",
    )
    SENTRY_PROFILE_LIFECYCLE: str | None = Field(
        default=None,
        description="Optional Sentry profile lifecycle mode, e.g. 'trace'",
    )
    SENTRY_SEND_PII: bool = Field(
        default=False, description="Send user IP/headers to Sentry"
    )

    # Feature flags / development toggles
    DEV_USER_FIREBASE_UID: str = Field(default="dev_firebase_uid")
    DEV_USER_EMAIL: str = Field(default="dev@example.com")
    DEV_USER_USERNAME: str = Field(default="dev_user")

    # Guest trial quota
    GUEST_INSTALL_HASH_SECRET: str = Field(
        default="",
        description="HMAC-SHA256 secret for hashing guest install IDs before Redis key creation. Must be set in production.",
    )

    # Additional fields from actual .env
    UPLOADS_DIR: str | None = Field(default=None)
    FCM_CREDENTIALS_PATH: str | None = Field(default=None)
    SMTP_HOST: str | None = Field(default=None)
    SMTP_PORT: int | None = Field(default=None)
    SMTP_USERNAME: str | None = Field(default=None)
    SMTP_PASSWORD: str | None = Field(default=None)
    EMAIL_FROM_ADDRESS: str | None = Field(default=None)
    EMAIL_FROM_NAME: str | None = Field(default=None)

    # Cloudflare Workers AI — text generation / AI fallback
    CLOUDFLARE_WORKERS_AI_ENABLED: bool = Field(
        default=True,
        description="Enable Cloudflare Workers AI as an alternate text-generation provider",
    )
    CLOUDFLARE_ACCOUNT_ID: str = Field(
        default="", description="Cloudflare account ID for Workers AI text API"
    )
    CLOUDFLARE_API_TOKEN: str = Field(
        default="", description="Cloudflare API token with Workers AI permission"
    )
    CLOUDFLARE_AI_GATEWAY_ID: str = Field(
        default="",
        description="AI Gateway ID for routing Workers AI requests (optional)",
    )
    CLOUDFLARE_WORKERS_AI_TEXT_MODEL: str = Field(
        default="@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        description="Workers AI model for text generation fallback",
    )
    CLOUDFLARE_WORKERS_AI_TEXT_PURPOSES: str = Field(
        default="recipe,general,meal_names,discovery,parse_text,barcode",
        description="Comma-separated ModelPurpose values where Workers AI is included in routing",
    )
    CLOUDFLARE_WORKERS_AI_JSON_MODE: bool = Field(
        default=True, description="Enable Workers AI JSON Mode for structured responses"
    )
    CLOUDFLARE_WORKERS_AI_TIMEOUT_SECONDS: int = Field(
        default=60,
        description="HTTP timeout for Workers AI requests (thinking models need ≥60s)",
    )
    CLOUDFLARE_WORKERS_AI_IMAGE_MODEL: str = Field(
        default="@cf/black-forest-labs/flux-2-klein-9b",
        description="Workers AI model for catalog meal image generation.",
    )
    CLOUDFLARE_WORKERS_AI_VISION_ENABLED: bool = Field(
        default=True,
        description="Enable Cloudflare Workers AI for vision tasks",
    )
    CLOUDFLARE_WORKERS_AI_VISION_MODEL: str = Field(
        default="@cf/google/gemma-4-26b-a4b-it",
        description="Workers AI model for image analysis (must support vision input)",
    )
    CLOUDFLARE_WORKERS_AI_VISION_PURPOSES: str = Field(
        default="meal_scan,food_label_scan,ingredient_scan",
        description="Comma-separated ModelPurpose values where Workers AI vision is included in routing",
    )
    # Meal analysis settings
    MEAL_ANALYZE_MAX_ATTEMPTS: int = Field(default=2)
    MEAL_ANALYZE_MAX_OUTPUT_TOKENS: int = Field(
        default=MEAL_ANALYZE_DEFAULT_MAX_OUTPUT_TOKENS
    )
    AI_MEAL_ANALYZE_GRAPH_ENABLED: bool = Field(
        default=False,
        description="Enable the LangGraph meal image analysis workflow.",
    )
    AI_MEAL_ANALYZE_FATSECRET_VALIDATION_ENABLED: bool = Field(
        default=False,
        description="Enable optional FatSecret nutrition reference validation for meal analysis.",
    )
    AI_MEAL_ANALYZE_EXTERNAL_PROVIDER_TIMEOUT_SECONDS: float = Field(
        default=5.0,
        description="Timeout for optional external reference providers in meal analysis.",
    )
    AI_MEAL_ANALYZE_GRAPH_VERSION: str = Field(
        default="v1",
        description="Meal analysis graph version emitted in workflow state.",
    )
    PARSE_TEXT_STRUCTURED_REFERENCE_ENABLED: bool = Field(
        default=False,
        description="Enable structured local/FatSecret resolution for parse-text.",
    )
    PARSE_TEXT_FATSECRET_TIMEOUT_SECONDS: float = Field(
        default=3.0,
        gt=0,
        description="Request-wide parse-text FatSecret deadline in seconds.",
    )

    # Catalog meal recommendations analytics
    MEAL_RECOMMENDATIONS_ANALYTICS_SALT: str = Field(
        default="",
        description="HMAC salt for PostHog recommendation identities; empty disables analytics.",
    )

    # Affiliate integration
    AFFILIATE_INTEGRATION_ENABLED: bool = Field(
        default=False,
        description="Enable affiliate code validation via nutree-affiliate internal API",
    )
    AFFILIATE_API_BASE_URL: str | None = Field(
        default=None,
        description="Base URL of the nutree-affiliate service (e.g. https://affiliate.nutree.app)",
    )
    AFFILIATE_INTERNAL_SECRET: str | None = Field(
        default=None,
        description="Shared HMAC-SHA256 secret for MealTrack↔nutree-affiliate internal calls",
    )
    AFFILIATE_CODE_VALIDATE_TIMEOUT_SECONDS: float = Field(
        default=10.0,
        description="Timeout for affiliate HTTP calls (validate + send_event). 10s covers vercel dev cold starts.",
    )

    # Referral system (multi-currency)
    REFERRAL_COMMISSIONS: dict = Field(
        default={"USD": 2, "VND": 50000, "EUR": 1.8, "default": 2},
        description="Commission per currency (code: amount in local units)",
    )
    EXCHANGE_RATES_TO_VND: dict = Field(
        default={"USD": 25000, "EUR": 27000, "default": 25000},
        description="Fixed exchange rates to VND for wallet conversion",
    )

    @field_validator("NUTRITION_PROVIDER_GLOBAL_RPM", mode="before")
    @classmethod
    def default_provider_rpm(cls, value: Any) -> Any:
        if value is None or value == "":
            return 60
        return value

    @field_validator("REFERRAL_COMMISSIONS", "EXCHANGE_RATES_TO_VND", mode="before")
    @classmethod
    def parse_json_dict(cls, v: Any) -> dict:
        """Parse JSON string from env var to dict."""
        if isinstance(v, str):
            return json.loads(v)
        return v

    def get_commission(self, currency: str) -> float:
        """Get commission amount for a currency, fallback to default."""
        return self.REFERRAL_COMMISSIONS.get(
            currency, self.REFERRAL_COMMISSIONS.get("default", 2)
        )

    def convert_to_vnd(self, amount: float, currency: str) -> int:
        """Convert amount to VND using fixed exchange rate."""
        if currency == "VND":
            return int(amount)
        rate = self.EXCHANGE_RATES_TO_VND.get(
            currency, self.EXCHANGE_RATES_TO_VND.get("default", 25000)
        )
        return int(amount * rate)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Allow extra fields without validation errors
    )

    @property
    def redis_url(self) -> str:
        """Return Redis connection URL.

        If REDIS_URL is set, it is used verbatim (supports username/password URLs).
        Otherwise, a URL is constructed from the component settings.
        """
        if self.REDIS_URL:
            return self.REDIS_URL

        protocol = "rediss" if self.REDIS_SSL else "redis"
        if self.REDIS_USERNAME and self.REDIS_PASSWORD:
            auth = f"{self.REDIS_USERNAME}:{self.REDIS_PASSWORD}@"
        elif self.REDIS_PASSWORD:
            auth = f":{self.REDIS_PASSWORD}@"
        else:
            auth = ""
        return f"{protocol}://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()


settings = get_settings()
