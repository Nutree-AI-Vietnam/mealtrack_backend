"""Planner routing is explicit despite generic or misconfigured CF purposes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.domain.model.ai.model_purpose import ModelPurpose
from src.infra.adapters.meal_generation_service import PURPOSE_MAP
from src.infra.services.ai.ai_model_manager import AIModelManager


def _settings():
    return SimpleNamespace(
        OPENAI_API_KEY="unit-key",
        OPENAI_TEXT_MODEL="planner-openai-model",
        OPENAI_VISION_MODEL="openai-vision-model",
        OPENAI_REQUEST_TIMEOUT_SECONDS=20,
        OPENAI_MAX_RETRIES=2,
        OPENAI_STORE_RESPONSES=False,
        OPENAI_PROMPT_CACHE_ENABLED=True,
        OPENAI_PROMPT_CACHE_RETENTION="",
        OPENAI_PROMPT_CACHE_KEY_PREFIX="unit",
        CLOUDFLARE_WORKERS_AI_ENABLED=True,
        CLOUDFLARE_ACCOUNT_ID="unit",
        CLOUDFLARE_API_TOKEN="unit",
        CLOUDFLARE_WORKERS_AI_TEXT_MODEL="cf-text",
        CLOUDFLARE_WORKERS_AI_TEXT_PURPOSES="general,meal_plan_adjustment",
        CLOUDFLARE_AI_GATEWAY_ID="",
        CLOUDFLARE_WORKERS_AI_JSON_MODE=True,
        CLOUDFLARE_WORKERS_AI_TIMEOUT_SECONDS=20,
        CLOUDFLARE_WORKERS_AI_VISION_ENABLED=True,
        CLOUDFLARE_WORKERS_AI_VISION_MODEL="cf-vision",
        CLOUDFLARE_WORKERS_AI_VISION_PURPOSES="meal_scan,meal_plan_adjustment",
    )


@pytest.mark.asyncio
async def test_planner_ignores_both_cf_prepend_paths_and_generic_fallback():
    openai = Mock(generate=AsyncMock(return_value={"ok": True}))
    cf = Mock(generate=AsyncMock(return_value={"unexpected": True}))
    with (
        patch(
            "src.infra.services.ai.ai_model_manager.OpenAIProvider", return_value=openai
        ),
        patch(
            "src.infra.services.ai.ai_model_manager.CloudflareWorkersAIProvider",
            return_value=cf,
        ),
    ):
        manager = AIModelManager(_settings())
    assert PURPOSE_MAP["meal_plan_adjustment"] is ModelPurpose.MEAL_PLAN_ADJUSTMENT
    assert manager.get_fallback_chain(ModelPurpose.MEAL_PLAN_ADJUSTMENT) == [
        "planner-openai-model"
    ]
    assert manager.get_fallback_chain(ModelPurpose.GENERAL)[0] == "cf-text"
    assert await manager.generate(
        purpose=ModelPurpose.MEAL_PLAN_ADJUSTMENT,
        prompt="request",
        system_message="rules",
    ) == {"ok": True}
    assert openai.generate.await_count == 1
    assert cf.generate.await_count == 0


@pytest.mark.asyncio
async def test_planner_missing_openai_cannot_use_cloudflare():
    from src.domain.exceptions.ai_exceptions import AIUnavailableError

    settings = _settings()
    settings.OPENAI_API_KEY = ""
    with patch(
        "src.infra.services.ai.ai_model_manager.CloudflareWorkersAIProvider"
    ) as cf:
        manager = AIModelManager(settings)
    with pytest.raises(AIUnavailableError, match="OpenAI"):
        await manager.generate(
            purpose=ModelPurpose.MEAL_PLAN_ADJUSTMENT,
            prompt="request",
            system_message="rules",
        )
    cf.return_value.generate.assert_not_called()
