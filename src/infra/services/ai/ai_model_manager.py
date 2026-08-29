"""Provider-agnostic AI model manager with fallback support."""

import logging
import threading
from typing import Any, Optional

from src.domain.exceptions.ai_exceptions import (
    AIOutputValidationError,
    AIUnavailableError,
)
from src.domain.model.ai.model_purpose import ModelPurpose
from src.domain.ports.ai_provider_port import AICapability
from src.infra.services.ai.ai_vision_errors import AIVisionError, AIVisionFailureKind
from src.infra.services.ai.provider_circuit_breaker import ProviderCircuitBreaker
from src.infra.services.ai.providers.cloudflare_workers_ai_provider import (
    CloudflareWorkersAIProvider,
)
from src.infra.services.ai.providers.openai_provider import OpenAIProvider
from src.observability import increment_metric, log_event

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_MODEL = "gpt-5.4-mini-2026-03-17"
TEXT_PURPOSES: set[ModelPurpose] = {
    ModelPurpose.PARSE_TEXT,
    ModelPurpose.BARCODE,
    ModelPurpose.MEAL_NAMES,
    ModelPurpose.RECIPE,
    ModelPurpose.DISCOVERY,
    ModelPurpose.GENERAL,
}
VISION_PURPOSES: set[ModelPurpose] = {
    ModelPurpose.MEAL_SCAN,
    ModelPurpose.FOOD_LABEL_SCAN,
    ModelPurpose.INGREDIENT_SCAN,
}

FALLBACK_CHAINS: dict[ModelPurpose, list[str]] = {
    # ==========================================================================
    # VISION TASKS: OpenAI primary, optional provider routes append as fallback
    # ==========================================================================
    ModelPurpose.MEAL_SCAN: [
        DEFAULT_OPENAI_MODEL,
    ],
    ModelPurpose.FOOD_LABEL_SCAN: [
        DEFAULT_OPENAI_MODEL,
    ],
    ModelPurpose.INGREDIENT_SCAN: [
        DEFAULT_OPENAI_MODEL,
    ],
    # ==========================================================================
    # SHORT STRUCTURED TEXT: OpenAI fallback when CF text is configured
    # ==========================================================================
    ModelPurpose.PARSE_TEXT: [
        DEFAULT_OPENAI_MODEL,
    ],
    ModelPurpose.BARCODE: [DEFAULT_OPENAI_MODEL],
    ModelPurpose.MEAL_NAMES: [
        DEFAULT_OPENAI_MODEL,
    ],
    ModelPurpose.DISCOVERY: [
        DEFAULT_OPENAI_MODEL,
    ],
    ModelPurpose.GENERAL: [DEFAULT_OPENAI_MODEL],
    # ==========================================================================
    # RECIPE TASKS: OpenAI fallback when CF text is configured
    # ==========================================================================
    ModelPurpose.RECIPE: [
        DEFAULT_OPENAI_MODEL,
    ],
}


class AIModelManager:
    """
    Provider-agnostic AI model manager.

    Coordinates multiple providers through AIProviderPort interface.
    Uses circuit breaker for health tracking and automatic fallback.
    """

    _instance: Optional["AIModelManager"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "AIModelManager":
        """Get singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing."""
        with cls._lock:
            cls._instance = None

    def __init__(self, settings=None) -> None:
        from src.infra.config.settings import get_settings

        _settings = settings if settings is not None else get_settings()

        self._circuit_breaker = ProviderCircuitBreaker()
        self._providers: dict[str, Any] = {}
        self._model_provider_overrides: dict[str, str] = {}

        # Start with a mutable copy of base OpenAI-first chains.
        self._fallback_chains: dict[ModelPurpose, list[str]] = {
            p: list(chain) for p, chain in FALLBACK_CHAINS.items()
        }

        self._maybe_add_cf_provider(_settings)
        self._maybe_add_openai_provider(_settings)

    def _maybe_add_openai_provider(self, settings) -> None:
        """Instantiate OpenAI when configured and map explicit model ownership."""
        if not settings.OPENAI_API_KEY:
            return

        openai = OpenAIProvider(
            api_key=settings.OPENAI_API_KEY,
            request_timeout_seconds=settings.OPENAI_REQUEST_TIMEOUT_SECONDS,
            max_retries=settings.OPENAI_MAX_RETRIES,
            store_responses=settings.OPENAI_STORE_RESPONSES,
            prompt_cache_enabled=settings.OPENAI_PROMPT_CACHE_ENABLED,
            prompt_cache_retention=settings.OPENAI_PROMPT_CACHE_RETENTION or None,
            prompt_cache_key_prefix=settings.OPENAI_PROMPT_CACHE_KEY_PREFIX,
        )
        self._providers["openai"] = openai
        self._model_provider_overrides[settings.OPENAI_TEXT_MODEL] = "openai"
        self._model_provider_overrides[settings.OPENAI_VISION_MODEL] = "openai"

        self._append_model_for_purposes(
            settings.OPENAI_TEXT_MODEL,
            TEXT_PURPOSES - {ModelPurpose.PARSE_TEXT},
            remove_models={DEFAULT_OPENAI_MODEL, settings.OPENAI_TEXT_MODEL},
        )
        self._prepend_model_for_purposes(
            settings.OPENAI_TEXT_MODEL,
            {ModelPurpose.PARSE_TEXT},
            remove_models={DEFAULT_OPENAI_MODEL, settings.OPENAI_TEXT_MODEL},
        )
        self._prepend_model_for_purposes(
            settings.OPENAI_VISION_MODEL,
            VISION_PURPOSES,
            remove_models={DEFAULT_OPENAI_MODEL, settings.OPENAI_VISION_MODEL},
        )

    def _prepend_model_for_purposes(
        self,
        model: str,
        purposes: set[ModelPurpose],
        *,
        remove_models: set[str] | None = None,
    ) -> None:
        for purpose in purposes:
            chain = self._fallback_chains[purpose]
            for existing_model in remove_models or {model}:
                while existing_model in chain:
                    chain.remove(existing_model)
            chain.insert(0, model)

    def _maybe_add_cf_provider(self, settings) -> None:
        """Instantiate and wire Workers AI provider when all required settings are present."""
        if not (
            settings.CLOUDFLARE_WORKERS_AI_ENABLED
            and settings.CLOUDFLARE_ACCOUNT_ID
            and settings.CLOUDFLARE_API_TOKEN
            and settings.CLOUDFLARE_WORKERS_AI_TEXT_MODEL
        ):
            return

        vision_enabled = getattr(
            settings, "CLOUDFLARE_WORKERS_AI_VISION_ENABLED", False
        )
        vision_model = getattr(settings, "CLOUDFLARE_WORKERS_AI_VISION_MODEL", "")
        vision_purposes = getattr(settings, "CLOUDFLARE_WORKERS_AI_VISION_PURPOSES", "")

        cf = CloudflareWorkersAIProvider(
            account_id=settings.CLOUDFLARE_ACCOUNT_ID,
            api_token=settings.CLOUDFLARE_API_TOKEN,
            text_model=settings.CLOUDFLARE_WORKERS_AI_TEXT_MODEL,
            gateway_id=settings.CLOUDFLARE_AI_GATEWAY_ID or "",
            json_mode_enabled=settings.CLOUDFLARE_WORKERS_AI_JSON_MODE,
            timeout_seconds=settings.CLOUDFLARE_WORKERS_AI_TIMEOUT_SECONDS,
            vision_model=vision_model,
            vision_enabled=vision_enabled,
        )
        self._providers["cloudflare-workers-ai"] = cf
        self._model_provider_overrides[settings.CLOUDFLARE_WORKERS_AI_TEXT_MODEL] = (
            "cloudflare-workers-ai"
        )
        self._append_cf_to_text_chains(
            cf_model=settings.CLOUDFLARE_WORKERS_AI_TEXT_MODEL,
            text_purposes_csv=settings.CLOUDFLARE_WORKERS_AI_TEXT_PURPOSES,
        )

        if vision_enabled and vision_model:
            self._model_provider_overrides[vision_model] = "cloudflare-workers-ai"
            self._append_cf_to_vision_chains(
                cf_model=vision_model,
                vision_purposes_csv=vision_purposes,
            )
            logger.info(
                "[CF-WORKERS-AI-VISION-ENABLED] purposes=%s model=%s",
                vision_purposes,
                vision_model,
            )

        logger.info(
            "[CF-WORKERS-AI-ENABLED] purposes=%s model=%s",
            settings.CLOUDFLARE_WORKERS_AI_TEXT_PURPOSES,
            settings.CLOUDFLARE_WORKERS_AI_TEXT_MODEL,
        )

    def _append_cf_to_text_chains(self, cf_model: str, text_purposes_csv: str) -> None:
        """Prepend raw CF model id to configured text-purpose chains, appending for PARSE_TEXT."""
        configured = {
            p.strip().lower() for p in text_purposes_csv.split(",") if p.strip()
        }
        for purpose in ModelPurpose:
            if purpose.value in configured:
                if purpose == ModelPurpose.PARSE_TEXT:
                    self._append_model_for_purposes(cf_model, {purpose})
                else:
                    self._prepend_model_for_purposes(cf_model, {purpose})

    def _append_cf_to_vision_chains(
        self, cf_model: str, vision_purposes_csv: str
    ) -> None:
        """Append CF vision model to configured vision-purpose chains."""
        configured = {
            p.strip().lower() for p in vision_purposes_csv.split(",") if p.strip()
        }
        valid_values = {mp.value for mp in ModelPurpose}
        unknown = configured - valid_values
        if unknown:
            logger.warning(
                "[CF-WORKERS-AI-VISION] unknown purposes ignored: %s",
                ", ".join(sorted(unknown)),
            )
        for purpose in ModelPurpose:
            if purpose.value in configured:
                self._append_model_for_purposes(cf_model, {purpose})

    def _append_model_for_purposes(
        self,
        model: str,
        purposes: set[ModelPurpose],
        *,
        remove_models: set[str] | None = None,
    ) -> None:
        for purpose in purposes:
            chain = self._fallback_chains[purpose]
            for existing_model in remove_models or {model}:
                while existing_model in chain:
                    chain.remove(existing_model)
            chain.append(model)

    def get_fallback_chain(self, purpose: ModelPurpose) -> list[str]:
        """Get fallback chain for a purpose."""
        return self._fallback_chains.get(
            purpose, self._fallback_chains[ModelPurpose.GENERAL]
        ).copy()

    def _get_provider_for_model(self, model: str):
        """Get provider that owns a model, checking explicit overrides before prefix heuristics."""
        if model in self._model_provider_overrides:
            return self._providers.get(self._model_provider_overrides[model])
        return None

    async def generate(
        self,
        purpose: ModelPurpose,
        prompt: str,
        system_message: str,
        response_type: str = "json",
        max_tokens: int | None = None,
        schema: type | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Generate with automatic fallback.

        Tries each model in the fallback chain until one succeeds.
        Records failures/successes in circuit breaker.
        """
        chain = self.get_fallback_chain(purpose)
        available = self._circuit_breaker.filter_available(chain)

        if not available:
            logger.warning(
                f"[ALL-CIRCUITS-OPEN] purpose={purpose.value} | forcing first model"
            )
            available = [chain[0]]

        attempted = []
        last_error = None

        for model in available:
            provider = self._get_provider_for_model(model)
            if provider is None:
                continue

            attempted.append(model)

            try:
                logger.debug(f"[AI-ATTEMPT] purpose={purpose.value} | model={model}")

                result = await provider.generate(
                    model=model,
                    prompt=prompt,
                    system_message=system_message,
                    response_type=response_type,
                    max_tokens=max_tokens,
                    schema=schema,
                    purpose_hint=purpose.value,
                    **kwargs,
                )

                self._circuit_breaker.record_success(model)

                if model != chain[0]:
                    logger.info(
                        f"[AI-FALLBACK-SUCCESS] purpose={purpose.value} | "
                        f"failed={attempted[:-1]} | succeeded={model}"
                    )

                return result

            except Exception as e:
                last_error = str(e)
                error_code = provider.extract_error_code(e)

                if self._circuit_breaker.should_trip(error_code):
                    self._circuit_breaker.record_failure(model)

                logger.warning(
                    f"[AI-ATTEMPT-FAILED] purpose={purpose.value} | "
                    f"model={model} | error={last_error[:100]}"
                )

        log_event(
            "warning",
            "ai.provider.failure",
            attributes={
                "component": "ai_model_manager",
                "attempt_count": len(attempted),
            },
        )
        raise AIUnavailableError(
            f"All models failed for {purpose.value}",
            attempted_models=attempted,
            last_error=last_error,
        )

    async def generate_with_vision(
        self,
        purpose: ModelPurpose,
        prompt: str,
        image_data: bytes,
        system_message: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate with vision, with automatic fallback."""
        chain = self.get_fallback_chain(purpose)
        available = self._circuit_breaker.filter_available(chain)

        if not available:
            available = [chain[0]]

        attempted = []
        last_error = None
        deterministic_failures: list[AIVisionError] = []
        has_transient_failure = False

        for model in available:
            provider = self._get_provider_for_model(model)
            if provider is None:
                continue

            if AICapability.VISION not in provider.supported_capabilities:
                continue

            attempted.append(model)
            increment_metric(
                "ai.vision.provider.attempt.count",
                attributes={
                    "ai_provider": provider.provider_name,
                    "ai_model": model,
                    "ai_purpose": purpose.value,
                },
            )

            provider_kwargs = dict(kwargs)
            if "schema" in kwargs:
                provider_kwargs["schema"] = kwargs["schema"]

            try:
                result = await provider.generate_with_vision(
                    model=model,
                    prompt=prompt,
                    image_data=image_data,
                    system_message=system_message,
                    purpose_hint=purpose.value,  # NEW
                    **provider_kwargs,
                )

                self._circuit_breaker.record_success(model)

                if len(attempted) >= 2:
                    # This model is not the first in the chain — a fallback occurred
                    increment_metric(
                        "ai.vision.fallback.count",
                        attributes={
                            "ai_purpose": purpose.value,
                            "fallback_from": attempted[-2],
                            "fallback_to": model,
                        },
                    )

                return result

            except Exception as e:
                last_error = str(e)

                if isinstance(e, AIVisionError) and e.kind in (
                    AIVisionFailureKind.schema_validation,
                    AIVisionFailureKind.json_parse,
                ):
                    deterministic_failures.append(e)
                    # Deterministic failure — schema/parse errors won't fix with same model;
                    # skip circuit breaker recording and advance to next provider
                    logger.warning(
                        "[AI-VISION-SCHEMA-FAIL] purpose=%s model=%s kind=%s",
                        purpose.value,
                        model,
                        e.kind.value,
                    )
                    metric_name = (
                        "ai.vision.schema_validation_failure.count"
                        if e.kind == AIVisionFailureKind.schema_validation
                        else "ai.vision.parse_failure.count"
                    )
                    increment_metric(
                        metric_name,
                        attributes={
                            "ai_provider": e.provider,
                            "ai_model": model,
                            "ai_purpose": purpose.value,
                            "failure_kind": e.kind.value,
                        },
                    )
                    log_event(
                        "warning",
                        "ai.vision.classified_failure",
                        attributes={
                            "ai_provider": e.provider,
                            "ai_model": model,
                            "ai_purpose": purpose.value,
                            "ai_stage": "provider_call",
                            "failure_kind": e.kind.value,
                        },
                    )
                    continue

                # Transient/unknown — use circuit breaker as before
                has_transient_failure = True
                error_code = provider.extract_error_code(e)
                if self._circuit_breaker.should_trip(error_code):
                    self._circuit_breaker.record_failure(model)
                increment_metric(
                    "ai.vision.provider.failure.count",
                    attributes={
                        "ai_provider": provider.provider_name,
                        "ai_model": model,
                        "ai_purpose": purpose.value,
                    },
                )
                logger.warning(
                    "[AI-ATTEMPT-FAILED] purpose=%s model=%s error=%s",
                    purpose.value,
                    model,
                    last_error[:100],
                )

        increment_metric(
            "ai.vision.request.failure.count",
            attributes={"ai_purpose": purpose.value, "attempt_count": len(attempted)},
        )
        if deterministic_failures and not has_transient_failure:
            log_event(
                "warning",
                "ai.vision.output_validation.failure",
                attributes={
                    "component": "ai_model_manager",
                    "attempt_count": len(attempted),
                    "ai_purpose": purpose.value,
                },
            )
            raise AIOutputValidationError(
                "Invalid AI structured output",
                purpose=purpose.value,
                attempt_count=len(attempted),
                validation_details=[
                    _vision_failure_detail(failure)
                    for failure in deterministic_failures[:5]
                ],
            )
        log_event(
            "warning",
            "ai.provider.failure",
            attributes={
                "component": "ai_model_manager",
                "attempt_count": len(attempted),
            },
        )
        raise AIUnavailableError(
            f"All vision models failed for {purpose.value}",
            attempted_models=attempted,
            last_error=last_error,
        )


def _vision_failure_detail(error: AIVisionError) -> str:
    provider = error.provider or "vision-provider"
    model = error.model or "unknown-model"
    detail = f"{provider}/{model}: {error.kind.value}"
    if error.validation_details:
        detail = f"{detail}: {'; '.join(error.validation_details[:3])}"
    return detail
