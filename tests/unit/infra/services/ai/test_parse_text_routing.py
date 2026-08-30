"""Tests for parse_text routing in AIModelManager."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.domain.model.ai.model_purpose import ModelPurpose
from src.infra.services.ai.ai_model_manager import AIModelManager


def _mock_settings(*, cf_enabled: bool = False, cf_purposes: str = "parse_text,recipe"):
    s = Mock()
    s.AI_PRIMARY_PROVIDER = "openai"
    s.AI_FALLBACK_PROVIDER = "cloudflare-workers-ai"
    s.OPENAI_API_KEY = "test-openai-key"
    s.OPENAI_TEXT_MODEL = "gpt-5.4-mini-2026-03-17"
    s.OPENAI_PARSE_TEXT_MODEL = "gpt-5.6-luna-2026-06-01"
    s.OPENAI_VISION_MODEL = "gpt-5.4-vision-2026-03-17"
    s.OPENAI_REQUEST_TIMEOUT_SECONDS = 20
    s.OPENAI_MAX_RETRIES = 1
    s.OPENAI_STORE_RESPONSES = False
    s.OPENAI_PROMPT_CACHE_ENABLED = True
    s.OPENAI_PROMPT_CACHE_RETENTION = ""
    s.OPENAI_PROMPT_CACHE_KEY_PREFIX = "mealtrack"
    s.CLOUDFLARE_WORKERS_AI_ENABLED = cf_enabled
    s.CLOUDFLARE_ACCOUNT_ID = "cf-account" if cf_enabled else ""
    s.CLOUDFLARE_API_TOKEN = "cf-token" if cf_enabled else ""
    s.CLOUDFLARE_WORKERS_AI_TEXT_MODEL = (
        "@cf/meta/llama-3.1-8b-instruct" if cf_enabled else ""
    )
    s.CLOUDFLARE_WORKERS_AI_TEXT_PURPOSES = cf_purposes if cf_enabled else ""
    s.CLOUDFLARE_WORKERS_AI_JSON_MODE = False
    s.CLOUDFLARE_WORKERS_AI_TIMEOUT_SECONDS = 30
    s.CLOUDFLARE_AI_GATEWAY_ID = ""
    s.CLOUDFLARE_WORKERS_AI_VISION_ENABLED = False
    s.CLOUDFLARE_WORKERS_AI_VISION_MODEL = ""
    s.CLOUDFLARE_WORKERS_AI_VISION_PURPOSES = ""
    return s


def test_parse_text_routing_openai_only():
    settings = _mock_settings(cf_enabled=False)
    with patch("src.infra.services.ai.ai_model_manager.OpenAIProvider"):
        manager = AIModelManager(settings)
        chain = manager.get_fallback_chain(ModelPurpose.PARSE_TEXT)
        assert chain == ["gpt-5.6-luna-2026-06-01"]


def test_parse_text_routing_luna_primary_cf_fallback():
    settings = _mock_settings(cf_enabled=True, cf_purposes="parse_text,recipe")
    with (
        patch("src.infra.services.ai.ai_model_manager.OpenAIProvider"),
        patch("src.infra.services.ai.ai_model_manager.CloudflareWorkersAIProvider"),
    ):
        manager = AIModelManager(settings)
        parse_chain = manager.get_fallback_chain(ModelPurpose.PARSE_TEXT)
        assert parse_chain == [
            "gpt-5.6-luna-2026-06-01",
            "@cf/meta/llama-3.1-8b-instruct",
        ]

        # Contrast with other text purposes where CF is prepended
        recipe_chain = manager.get_fallback_chain(ModelPurpose.RECIPE)
        assert recipe_chain == [
            "@cf/meta/llama-3.1-8b-instruct",
            "gpt-5.4-mini-2026-03-17",
        ]


@pytest.mark.asyncio
async def test_parse_text_falls_back_to_cloudflare_on_primary_failure():
    settings = _mock_settings(cf_enabled=True, cf_purposes="parse_text")

    openai_mock = Mock()
    openai_mock.generate = AsyncMock(
        side_effect=RuntimeError("Luna connection timeout")
    )
    openai_mock.extract_error_code = Mock(return_value="TIMEOUT")

    cf_mock = Mock()
    cf_mock.generate = AsyncMock(
        return_value={
            "emoji": "🍜",
            "items": [
                {
                    "name": "Phở bò",
                    "lookup_name": "Beef pho",
                    "preparation": "unknown",
                    "quantity": 1,
                    "unit": "tô",
                    "english_unit": "bowl",
                    "quantity_g": 500,
                    "macros": {
                        "protein_g": 30,
                        "carbs_g": 60,
                        "fat_g": 12,
                        "fiber_g": 3,
                        "sugar_g": 2,
                    },
                }
            ],
        }
    )

    with (
        patch(
            "src.infra.services.ai.ai_model_manager.OpenAIProvider",
            return_value=openai_mock,
        ),
        patch(
            "src.infra.services.ai.ai_model_manager.CloudflareWorkersAIProvider",
            return_value=cf_mock,
        ),
    ):
        manager = AIModelManager(settings)
        result = await manager.generate(
            purpose=ModelPurpose.PARSE_TEXT,
            prompt="language: vi\nmeal: 1 tô phở bò",
            system_message="system prompt",
        )

        assert result["emoji"] == "🍜"
        assert len(result["items"]) == 1
        assert result["items"][0]["name"] == "Phở bò"
        assert openai_mock.generate.called
        assert cf_mock.generate.called
