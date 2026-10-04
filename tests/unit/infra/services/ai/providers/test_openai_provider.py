from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from openai import BadRequestError
from pydantic import ValidationError

from src.domain.exceptions.ai_exceptions import AIContentRejectedError
from src.domain.model.ai.nutrition_contracts import VisionNutritionResponse
from src.domain.ports.ai_provider_port import AICapability
from src.infra.services.ai.ai_vision_errors import AIVisionError, AIVisionFailureKind
from src.infra.services.ai.langchain_openai_adapter import LangChainOpenAIResult
from src.infra.services.ai.providers.openai_provider import OpenAIProvider


def _provider(store_responses=False):
    return OpenAIProvider(
        api_key="test-key",
        request_timeout_seconds=20,
        max_retries=1,
        store_responses=store_responses,
        prompt_cache_enabled=True,
        prompt_cache_retention="in_memory",
        prompt_cache_key_prefix="mealtrack-test",
    )


def _parsed_vision_response():
    return VisionNutritionResponse.model_validate(
        {
            "is_food": True,
            "dish_name": "Chicken rice bowl",
            "emoji": "🍚",
            "foods": [
                {
                    "name": "grilled chicken",
                    "quantity_g": 150.0,
                    "macros": {
                        "protein_g": 35.0,
                        "carbs_g": 0.0,
                        "fat_g": 5.0,
                    },
                    "confidence": 0.92,
                }
            ],
            "confidence": 0.88,
        }
    )


def test_openai_provider_capabilities():
    provider = _provider()

    assert provider.provider_name == "openai"
    assert AICapability.TEXT_GENERATION in provider.supported_capabilities
    assert AICapability.VISION in provider.supported_capabilities
    assert AICapability.STRUCTURED_OUTPUT in provider.supported_capabilities


@pytest.mark.asyncio
async def test_generate_structured_text_calls_adapter_with_prompt_cache_kwargs():
    provider = _provider()
    parsed = _parsed_vision_response()
    raw_message = SimpleNamespace()
    provider._langchain.generate_structured = AsyncMock(
        return_value=LangChainOpenAIResult(parsed=parsed, raw_message=raw_message)
    )

    result = await provider.generate(
        model="gpt-5.4-mini-2026-03-17",
        prompt="Chicken rice bowl",
        system_message="Return canonical JSON.",
        schema=VisionNutritionResponse,
        max_tokens=1500,
        purpose_hint="parse_text",
    )

    assert result["emoji"] == "🍚"
    call_kwargs = provider._langchain.generate_structured.await_args.kwargs
    assert call_kwargs["model"] == "gpt-5.4-mini-2026-03-17"
    assert call_kwargs["prompt"] == "Chicken rice bowl"
    assert call_kwargs["system_message"] == "Return canonical JSON."
    assert call_kwargs["schema"] is VisionNutritionResponse
    assert call_kwargs["max_tokens"] == 1500
    assert call_kwargs["request_kwargs"]["prompt_cache_key"].startswith(
        "mealtrack-test:parse_text:"
    )
    assert call_kwargs["request_kwargs"]["prompt_cache_retention"] == "in_memory"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_metadata",
    [
        {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
        {"status": "completed"},
    ],
)
async def test_generate_structured_result_classifies_responses_completion_metadata(
    response_metadata,
):
    provider = _provider()
    raw_message = SimpleNamespace(response_metadata=response_metadata)
    provider._langchain.generate_structured = AsyncMock(
        return_value=LangChainOpenAIResult(
            parsed={"items": []},
            raw_message=raw_message,
        )
    )

    result = await provider.generate_structured_result(
        model="gpt-5.4-mini-2026-03-17",
        prompt="Translate.",
        system_message="Translate.",
        schema=VisionNutritionResponse,
        purpose_hint="translation",
        store_responses=False,
    )

    assert result.incomplete is (response_metadata["status"] == "incomplete")


@pytest.mark.asyncio
async def test_generate_json_without_schema_parses_raw_content():
    provider = _provider()
    raw_message = SimpleNamespace()
    provider._langchain.generate_raw = AsyncMock(
        return_value=LangChainOpenAIResult(
            parsed={"raw_content": '{"items":[{"name":"pho"}]}'},
            raw_message=raw_message,
        )
    )

    result = await provider.generate(
        model="gpt-5.4-mini-2026-03-17",
        prompt="Generate summary.",
        system_message="Be concise.",
        max_tokens=600,
        purpose_hint="general",
    )

    assert result == {"items": [{"name": "pho"}]}
    call_kwargs = provider._langchain.generate_raw.await_args.kwargs
    assert call_kwargs["model"] == "gpt-5.4-mini-2026-03-17"
    assert call_kwargs["prompt"] == "Generate summary."
    assert call_kwargs["system_message"] == "Be concise."
    assert call_kwargs["max_tokens"] == 600
    assert call_kwargs["request_kwargs"]["prompt_cache_key"].startswith(
        "mealtrack-test:general:"
    )
    assert call_kwargs["request_kwargs"]["prompt_cache_retention"] == "in_memory"


@pytest.mark.asyncio
async def test_generate_text_without_schema_returns_raw_content():
    provider = _provider()
    raw_message = SimpleNamespace()
    provider._langchain.generate_raw = AsyncMock(
        return_value=LangChainOpenAIResult(
            parsed={"raw_content": "ok"},
            raw_message=raw_message,
        )
    )

    result = await provider.generate(
        model="gpt-5.4-mini-2026-03-17",
        prompt="Generate summary.",
        system_message="Be concise.",
        response_type="text",
        max_tokens=600,
        purpose_hint="general",
    )

    assert result == {"raw_content": "ok"}


@pytest.mark.asyncio
async def test_generate_with_vision_calls_adapter_with_prompt_cache_kwargs():
    provider = _provider()
    parsed = _parsed_vision_response()
    raw_message = SimpleNamespace()
    provider._langchain.generate_vision_structured = AsyncMock(
        return_value=LangChainOpenAIResult(parsed=parsed, raw_message=raw_message)
    )

    result = await provider.generate_with_vision(
        model="gpt-5.4-mini-2026-03-17",
        prompt="Identify food.",
        image_data=b"image-bytes",
        system_message="Return canonical JSON.",
        schema=VisionNutritionResponse,
        image_mime_type="image/png",
        max_tokens=1500,
        purpose_hint="meal_scan",
    )

    assert result["dish_name"] == "Chicken rice bowl"
    call_kwargs = provider._langchain.generate_vision_structured.await_args.kwargs
    assert call_kwargs["model"] == "gpt-5.4-mini-2026-03-17"
    assert call_kwargs["prompt"] == "Identify food."
    assert call_kwargs["image_data"] == b"image-bytes"
    assert call_kwargs["image_mime_type"] == "image/png"
    assert call_kwargs["system_message"] == "Return canonical JSON."
    assert call_kwargs["schema"] is VisionNutritionResponse
    assert call_kwargs["max_tokens"] == 1500
    assert call_kwargs["request_kwargs"]["prompt_cache_key"].startswith(
        "mealtrack-test:meal_scan:"
    )
    assert call_kwargs["request_kwargs"]["prompt_cache_retention"] == "in_memory"


@pytest.mark.asyncio
async def test_generate_with_vision_classifies_validation_errors():
    provider = _provider()
    validation_error = None
    try:
        VisionNutritionResponse.model_validate({"foods": []})
    except ValidationError as exc:
        validation_error = exc
    assert validation_error is not None
    provider._langchain.generate_vision_structured = AsyncMock(
        side_effect=validation_error
    )

    with pytest.raises(AIVisionError) as exc_info:
        await provider.generate_with_vision(
            model="gpt-5.4-mini-2026-03-17",
            prompt="Identify food.",
            image_data=b"image-bytes",
            system_message="Return canonical JSON.",
            schema=VisionNutritionResponse,
            purpose_hint="meal_scan",
        )

    assert exc_info.value.kind == AIVisionFailureKind.schema_validation
    assert exc_info.value.provider == "openai"
    assert exc_info.value.model == "gpt-5.4-mini-2026-03-17"


def _bad_request(code):
    response = httpx.Response(
        400, request=httpx.Request("POST", "https://api.openai.com/v1/responses")
    )
    return BadRequestError(
        "Invalid prompt", response=response, body={"code": code, "message": "x"}
    )


@pytest.mark.asyncio
async def test_generate_maps_invalid_prompt_to_content_rejected():
    provider = _provider()
    provider._langchain.generate_structured = AsyncMock(
        side_effect=_bad_request("invalid_prompt")
    )

    with pytest.raises(AIContentRejectedError) as exc_info:
        await provider.generate(
            model="gpt-5.6-luna",
            prompt="flagged",
            system_message="Return JSON.",
            schema=VisionNutritionResponse,
        )

    assert exc_info.value.provider == "openai"
    assert exc_info.value.model == "gpt-5.6-luna"


@pytest.mark.asyncio
async def test_generate_with_vision_maps_invalid_prompt_to_content_rejected():
    provider = _provider()
    provider._langchain.generate_vision_structured = AsyncMock(
        side_effect=_bad_request("invalid_prompt")
    )

    with pytest.raises(AIContentRejectedError):
        await provider.generate_with_vision(
            model="gpt-5.4-mini-2026-03-17",
            prompt="Identify food.",
            image_data=b"image-bytes",
            system_message="Return JSON.",
            schema=VisionNutritionResponse,
        )


@pytest.mark.asyncio
async def test_generate_keeps_other_bad_requests():
    provider = _provider()
    provider._langchain.generate_structured = AsyncMock(
        side_effect=_bad_request("invalid_request_error")
    )

    with pytest.raises(BadRequestError):
        await provider.generate(
            model="gpt-5.6-luna",
            prompt="hi",
            system_message="Return JSON.",
            schema=VisionNutritionResponse,
        )


@pytest.mark.asyncio
async def test_records_prompt_cache_usage_metrics(monkeypatch):
    provider = _provider()
    parsed = _parsed_vision_response()
    raw_message = SimpleNamespace()
    provider._langchain.generate_structured = AsyncMock(
        return_value=LangChainOpenAIResult(parsed=parsed, raw_message=raw_message)
    )
    provider._langchain.input_tokens = Mock(return_value=1500)
    provider._langchain.cached_tokens = Mock(return_value=1024)
    metrics = []

    def capture_metric(name, value=1.0, *, unit=None, attributes=None):
        metrics.append(
            {
                "name": name,
                "value": value,
                "unit": unit,
                "attributes": attributes or {},
            }
        )

    monkeypatch.setattr(
        "src.infra.services.ai.providers.openai_provider.increment_metric",
        capture_metric,
    )

    await provider.generate(
        model="gpt-5.4-mini-2026-03-17",
        prompt="Parse this meal text.",
        system_message="Return canonical meal text JSON.",
        schema=VisionNutritionResponse,
        purpose_hint="parse_text",
    )

    provider._langchain.input_tokens.assert_called_once_with(raw_message)
    provider._langchain.cached_tokens.assert_called_once_with(raw_message)
    assert {
        "name": "ai.openai.prompt_cache.request.count",
        "value": 1.0,
        "unit": None,
        "attributes": {
            "ai_provider": "openai",
            "ai_model": "gpt-5.4-mini-2026-03-17",
            "ai_purpose": "parse_text",
            "cache_hit": "true",
        },
    } in metrics
    assert any(
        metric["name"] == "ai.openai.prompt_cache.cached_tokens"
        and metric["value"] == 1024
        and metric["unit"] == "token"
        for metric in metrics
    )
    assert any(
        metric["name"] == "ai.openai.prompt_cache.input_tokens"
        and metric["value"] == 1500
        and metric["unit"] == "token"
        for metric in metrics
    )


@pytest.mark.asyncio
async def test_records_raw_text_prompt_cache_usage_metrics(monkeypatch):
    provider = _provider()
    raw_message = SimpleNamespace()
    provider._langchain.generate_raw = AsyncMock(
        return_value=LangChainOpenAIResult(
            parsed={"raw_content": "ok"},
            raw_message=raw_message,
        )
    )
    provider._langchain.input_tokens = Mock(return_value=1300)
    provider._langchain.cached_tokens = Mock(return_value=0)
    metrics = []

    monkeypatch.setattr(
        "src.infra.services.ai.providers.openai_provider.increment_metric",
        lambda name, value=1.0, *, unit=None, attributes=None: metrics.append(
            {
                "name": name,
                "value": value,
                "unit": unit,
                "attributes": attributes or {},
            }
        ),
    )

    await provider.generate(
        model="gpt-5.4-mini-2026-03-17",
        prompt="Generate summary.",
        system_message="Be concise.",
        response_type="text",
        purpose_hint="general",
    )

    request_metric = next(
        metric
        for metric in metrics
        if metric["name"] == "ai.openai.prompt_cache.request.count"
    )
    provider._langchain.input_tokens.assert_called_once_with(raw_message)
    provider._langchain.cached_tokens.assert_called_once_with(raw_message)
    assert request_metric["attributes"]["cache_hit"] == "false"
    assert any(
        metric["name"] == "ai.openai.prompt_cache.input_tokens"
        and metric["value"] == 1300
        for metric in metrics
    )


@pytest.mark.asyncio
async def test_records_vision_prompt_cache_usage_metrics(monkeypatch):
    provider = _provider()
    parsed = _parsed_vision_response()
    raw_message = SimpleNamespace()
    provider._langchain.generate_vision_structured = AsyncMock(
        return_value=LangChainOpenAIResult(parsed=parsed, raw_message=raw_message)
    )
    provider._langchain.input_tokens = Mock(return_value=1800)
    provider._langchain.cached_tokens = Mock(return_value=900)
    metrics = []

    monkeypatch.setattr(
        "src.infra.services.ai.providers.openai_provider.increment_metric",
        lambda name, value=1.0, *, unit=None, attributes=None: metrics.append(
            {
                "name": name,
                "value": value,
                "unit": unit,
                "attributes": attributes or {},
            }
        ),
    )

    await provider.generate_with_vision(
        model="gpt-5.4-mini-2026-03-17",
        prompt="Identify food.",
        image_data=b"image-bytes",
        system_message="Return canonical JSON.",
        schema=VisionNutritionResponse,
        purpose_hint="meal_scan",
    )

    provider._langchain.input_tokens.assert_called_once_with(raw_message)
    provider._langchain.cached_tokens.assert_called_once_with(raw_message)
    assert any(
        metric["name"] == "ai.openai.prompt_cache.cached_tokens"
        and metric["value"] == 900
        and metric["attributes"]["ai_purpose"] == "meal_scan"
        and metric["attributes"]["cache_hit"] == "true"
        for metric in metrics
    )
