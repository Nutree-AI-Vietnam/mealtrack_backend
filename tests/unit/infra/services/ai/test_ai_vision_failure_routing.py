"""Tests for AIModelManager vision failure-kind routing.

Phase 3: verifies that schema/parse failures advance to next provider without
tripping the circuit breaker, while transient failures DO trip it.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.domain.exceptions.ai_exceptions import (
    AIOutputValidationError,
    AIUnavailableError,
    AIVisionError,
    AIVisionFailureKind,
)
from src.domain.model.ai.nutrition_contracts import VisionNutritionResponse
from src.domain.ports.ai_provider_port import AICapability
from src.infra.services.ai.ai_model_manager import AIModelManager, ModelPurpose


def _fake_settings(cf_enabled=False):
    s = Mock()
    s.AI_PRIMARY_PROVIDER = "openai"
    s.AI_FALLBACK_PROVIDER = "cloudflare-workers-ai"
    s.OPENAI_API_KEY = "test-openai-key"
    s.OPENAI_TEXT_MODEL = "openai-text-model"
    s.OPENAI_PARSE_TEXT_MODEL = "openai-luna-model"
    s.OPENAI_VISION_MODEL = "openai-vision-model"
    s.OPENAI_REQUEST_TIMEOUT_SECONDS = 20
    s.OPENAI_MAX_RETRIES = 1
    s.OPENAI_STORE_RESPONSES = False
    s.OPENAI_PROMPT_CACHE_ENABLED = True
    s.OPENAI_PROMPT_CACHE_RETENTION = ""
    s.OPENAI_PROMPT_CACHE_KEY_PREFIX = "mealtrack"
    s.CLOUDFLARE_WORKERS_AI_ENABLED = cf_enabled
    s.CLOUDFLARE_ACCOUNT_ID = ""
    s.CLOUDFLARE_API_TOKEN = ""
    s.CLOUDFLARE_WORKERS_AI_TEXT_MODEL = ""
    s.CLOUDFLARE_WORKERS_AI_TEXT_PURPOSES = ""
    s.CLOUDFLARE_WORKERS_AI_JSON_MODE = False
    s.CLOUDFLARE_WORKERS_AI_TIMEOUT_SECONDS = 30
    s.CLOUDFLARE_AI_GATEWAY_ID = ""
    s.CLOUDFLARE_WORKERS_AI_VISION_ENABLED = False
    s.CLOUDFLARE_WORKERS_AI_VISION_MODEL = ""
    s.CLOUDFLARE_WORKERS_AI_VISION_PURPOSES = ""
    return s


def _fake_settings_with_cf_text_and_vision():
    settings = _fake_settings(cf_enabled=True)
    settings.CLOUDFLARE_ACCOUNT_ID = "cf-account"
    settings.CLOUDFLARE_API_TOKEN = "cf-token"
    settings.CLOUDFLARE_WORKERS_AI_TEXT_MODEL = "cf-text-model"
    settings.CLOUDFLARE_WORKERS_AI_TEXT_PURPOSES = (
        "recipe,general,meal_names,discovery,parse_text,barcode"
    )
    settings.CLOUDFLARE_WORKERS_AI_VISION_ENABLED = True
    settings.CLOUDFLARE_WORKERS_AI_VISION_MODEL = "cf-vision-model"
    settings.CLOUDFLARE_WORKERS_AI_VISION_PURPOSES = "meal_scan,ingredient_scan"
    return settings


def test_openai_provider_receives_prompt_cache_settings(mock_circuit_breaker):
    settings = _fake_settings()
    settings.OPENAI_PROMPT_CACHE_ENABLED = True
    settings.OPENAI_PROMPT_CACHE_RETENTION = "in_memory"
    settings.OPENAI_PROMPT_CACHE_KEY_PREFIX = "mealtrack-test"

    with patch(
        "src.infra.services.ai.ai_model_manager.ProviderCircuitBreaker",
        return_value=mock_circuit_breaker,
    ):
        with patch(
            "src.infra.services.ai.ai_model_manager.OpenAIProvider"
        ) as provider_cls:
            AIModelManager(settings=settings)

    provider_cls.assert_called_once()
    kwargs = provider_cls.call_args.kwargs
    assert kwargs["prompt_cache_enabled"] is True
    assert kwargs["prompt_cache_retention"] == "in_memory"
    assert kwargs["prompt_cache_key_prefix"] == "mealtrack-test"


def test_text_purposes_prefer_cf_and_fallback_to_openai(mock_circuit_breaker):
    settings = _fake_settings_with_cf_text_and_vision()

    with patch(
        "src.infra.services.ai.ai_model_manager.ProviderCircuitBreaker",
        return_value=mock_circuit_breaker,
    ):
        with patch("src.infra.services.ai.ai_model_manager.OpenAIProvider"):
            with patch(
                "src.infra.services.ai.ai_model_manager.CloudflareWorkersAIProvider"
            ):
                manager = AIModelManager(settings=settings)

    for purpose in {
        ModelPurpose.BARCODE,
        ModelPurpose.MEAL_NAMES,
        ModelPurpose.RECIPE,
        ModelPurpose.DISCOVERY,
        ModelPurpose.GENERAL,
    }:
        assert manager.get_fallback_chain(purpose)[:2] == [
            "cf-text-model",
            "openai-text-model",
        ]
    assert manager.get_fallback_chain(ModelPurpose.PARSE_TEXT)[:2] == [
        "openai-luna-model",
        "cf-text-model",
    ]


def test_image_scan_purposes_prefer_openai_and_fallback_to_cf(mock_circuit_breaker):
    settings = _fake_settings_with_cf_text_and_vision()

    with patch(
        "src.infra.services.ai.ai_model_manager.ProviderCircuitBreaker",
        return_value=mock_circuit_breaker,
    ):
        with patch("src.infra.services.ai.ai_model_manager.OpenAIProvider"):
            with patch(
                "src.infra.services.ai.ai_model_manager.CloudflareWorkersAIProvider"
            ):
                manager = AIModelManager(settings=settings)

    for purpose in {
        ModelPurpose.MEAL_SCAN,
        ModelPurpose.INGREDIENT_SCAN,
    }:
        assert manager.get_fallback_chain(purpose)[:2] == [
            "openai-vision-model",
            "cf-vision-model",
        ]


@pytest.fixture(autouse=True)
def reset_manager():
    AIModelManager.reset_instance()
    yield
    AIModelManager.reset_instance()


@pytest.fixture
def mock_circuit_breaker():
    breaker = Mock()
    breaker.filter_available = Mock(side_effect=lambda models: models)
    breaker.record_failure = Mock()
    breaker.record_success = Mock()
    breaker.should_trip = Mock(return_value=True)
    return breaker


@pytest.fixture
def mock_openai_provider():
    provider = Mock()
    provider.provider_name = "openai"
    provider.supported_capabilities = {
        AICapability.VISION,
        AICapability.TEXT_GENERATION,
    }
    provider.generate_with_vision = AsyncMock(return_value={"result": "vision_success"})
    provider.extract_error_code = Mock(return_value=503)
    return provider


@pytest.fixture
def mock_cf_vision_provider():
    provider = Mock()
    provider.provider_name = "cloudflare-workers-ai"
    provider.supported_capabilities = {AICapability.VISION}
    provider.generate_with_vision = AsyncMock(
        return_value={"result": "cf_vision_success"}
    )
    provider.extract_error_code = Mock(return_value=503)
    return provider


def _build_manager(
    mock_circuit_breaker,
    mock_openai_provider,
    extra_providers=None,
    cf_vision_model=None,
):
    """Construct AIModelManager with injected mocks bypassing real provider init."""
    with patch(
        "src.infra.services.ai.ai_model_manager.OpenAIProvider",
        return_value=mock_openai_provider,
    ):
        with patch(
            "src.infra.services.ai.ai_model_manager.ProviderCircuitBreaker",
            return_value=mock_circuit_breaker,
        ):
            mgr = AIModelManager(settings=_fake_settings())

    # Replace internals with mocks
    mgr._circuit_breaker = mock_circuit_breaker
    mgr._providers = {"openai": mock_openai_provider}
    mgr._model_provider_overrides = {"openai-vision-model": "openai"}

    # Set an explicit chain so routing tests can advance through providers,
    # independent of the production FALLBACK_CHAINS.
    mgr._fallback_chains[ModelPurpose.MEAL_SCAN] = ["openai-vision-model"]

    if extra_providers:
        mgr._providers.update(extra_providers)

    if cf_vision_model:
        model_name, provider = cf_vision_model
        mgr._model_provider_overrides[model_name] = "cloudflare-workers-ai"
        mgr._fallback_chains[ModelPurpose.MEAL_SCAN].insert(0, model_name)

    return mgr


@pytest.fixture
def manager(mock_circuit_breaker, mock_openai_provider):
    return _build_manager(mock_circuit_breaker, mock_openai_provider)


@pytest.fixture
def manager_with_cf_vision(
    mock_circuit_breaker, mock_openai_provider, mock_cf_vision_provider
):
    return _build_manager(
        mock_circuit_breaker,
        mock_openai_provider,
        extra_providers={"cloudflare-workers-ai": mock_cf_vision_provider},
        cf_vision_model=("cf-vision-model", mock_cf_vision_provider),
    )


class TestVisionFailureKindRouting:
    @pytest.mark.asyncio
    async def test_schema_fail_advances_without_circuit_break(
        self,
        manager_with_cf_vision,
        mock_openai_provider,
        mock_circuit_breaker,
        mock_cf_vision_provider,
    ):
        """Schema validation failure must advance to next provider without tripping circuit breaker."""
        mock_circuit_breaker.filter_available = Mock(side_effect=lambda models: models)
        mock_cf_vision_provider.generate_with_vision = AsyncMock(
            side_effect=AIVisionError(
                "[CF-WORKERS-AI-VISION-SCHEMA-FAIL] provider=cloudflare-workers-ai model=test-model",
                kind=AIVisionFailureKind.schema_validation,
                provider="cloudflare-workers-ai",
                model="test-model",
            )
        )
        mock_openai_provider.generate_with_vision = AsyncMock(
            return_value={"result": "openai_vision_success"}
        )

        result = await manager_with_cf_vision.generate_with_vision(
            purpose=ModelPurpose.MEAL_SCAN,
            prompt="analyze food",
            image_data=b"fake_image",
            schema=VisionNutritionResponse,
        )

        assert result == {"result": "openai_vision_success"}
        mock_openai_provider.generate_with_vision.assert_awaited()
        assert (
            mock_cf_vision_provider.generate_with_vision.await_args.kwargs["schema"]
            is VisionNutritionResponse
        )
        assert (
            mock_openai_provider.generate_with_vision.await_args.kwargs["schema"]
            is VisionNutritionResponse
        )
        mock_circuit_breaker.record_failure.assert_not_called()

    @pytest.mark.asyncio
    async def test_json_parse_fail_advances_without_circuit_break(
        self,
        manager_with_cf_vision,
        mock_openai_provider,
        mock_circuit_breaker,
        mock_cf_vision_provider,
    ):
        """JSON parse failure must advance to next provider without tripping circuit breaker."""
        mock_circuit_breaker.filter_available = Mock(side_effect=lambda models: models)
        mock_cf_vision_provider.generate_with_vision = AsyncMock(
            side_effect=AIVisionError(
                "[CF-WORKERS-AI-VISION-PARSE-FAIL]",
                kind=AIVisionFailureKind.json_parse,
                provider="cloudflare-workers-ai",
                model="test-model",
            )
        )
        mock_openai_provider.generate_with_vision = AsyncMock(
            return_value={"result": "openai_fallback"}
        )

        result = await manager_with_cf_vision.generate_with_vision(
            purpose=ModelPurpose.MEAL_SCAN,
            prompt="analyze food",
            image_data=b"fake_image",
        )

        assert result == {"result": "openai_fallback"}
        mock_circuit_breaker.record_failure.assert_not_called()

    @pytest.mark.asyncio
    async def test_schema_failures_exhausting_chain_raise_output_validation(
        self,
        manager,
        mock_openai_provider,
        mock_circuit_breaker,
    ):
        mock_circuit_breaker.filter_available = Mock(side_effect=lambda models: models)
        mock_openai_provider.generate_with_vision = AsyncMock(
            side_effect=AIVisionError(
                "[OPENAI-VISION-SCHEMA-FAIL] provider=openai model=test-model",
                kind=AIVisionFailureKind.schema_validation,
                provider="openai",
                model="test-model",
                validation_details=["product_name: Field required"],
            )
        )

        with pytest.raises(AIOutputValidationError) as exc_info:
            await manager.generate_with_vision(
                purpose=ModelPurpose.MEAL_SCAN,
                prompt="analyze food",
                image_data=b"fake_image",
                schema=VisionNutritionResponse,
            )

        assert exc_info.value.purpose == "meal_scan"
        assert exc_info.value.attempt_count == 1
        assert exc_info.value.validation_details == [
            "openai/test-model: schema_validation: product_name: Field required"
        ]
        mock_circuit_breaker.record_failure.assert_not_called()

    @pytest.mark.asyncio
    async def test_transient_fail_records_circuit_breaker(
        self, manager, mock_openai_provider, mock_circuit_breaker
    ):
        """Transient (non-AIVisionError) failures must record failure in circuit breaker."""
        mock_circuit_breaker.filter_available = Mock(side_effect=lambda models: models)
        mock_openai_provider.generate_with_vision = AsyncMock(
            side_effect=Exception("503 Service Unavailable")
        )
        mock_openai_provider.extract_error_code = Mock(return_value=503)
        mock_circuit_breaker.should_trip = Mock(return_value=True)

        with pytest.raises(AIUnavailableError):
            await manager.generate_with_vision(
                purpose=ModelPurpose.MEAL_SCAN,
                prompt="analyze food",
                image_data=b"fake_image",
            )

        mock_circuit_breaker.record_failure.assert_called()
