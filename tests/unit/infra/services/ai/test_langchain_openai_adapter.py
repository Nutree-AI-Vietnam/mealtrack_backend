from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from src.domain.model.ai.nutrition_contracts import VisionNutritionResponse
from src.infra.services.ai.langchain_openai_adapter import (
    LangChainOpenAIResult,
    OpenAILangChainAdapter,
)


def _parsed_vision_response() -> VisionNutritionResponse:
    return VisionNutritionResponse.model_validate(
        {
            "is_food": True,
            "dish_name": "Chicken rice bowl",
            "emoji": "rice",
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


def _adapter(monkeypatch, *, store_responses: bool = False):
    created = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.raw_message = SimpleNamespace(
                content=[
                    {"type": "text", "text": "raw "},
                    {"type": "other"},
                    "answer",
                ],
                usage_metadata={
                    "input_tokens": 1500,
                    "input_token_details": {"cache_read": 512},
                },
                response_metadata={
                    "token_usage": {
                        "prompt_tokens": 1500,
                        "prompt_tokens_details": {"cached_tokens": 512},
                    }
                },
            )
            self.ainvoke = AsyncMock(return_value=self.raw_message)
            self.structured = MagicMock()
            self.structured_raw_message = SimpleNamespace(
                content="",
                usage_metadata={
                    "input_tokens": 1800,
                    "input_token_details": {"cache_read": 1024},
                },
            )
            self.structured.ainvoke = AsyncMock(
                return_value={
                    "raw": self.structured_raw_message,
                    "parsed": _parsed_vision_response().model_dump(),
                    "parsing_error": None,
                }
            )
            created.append(self)

        def with_structured_output(self, schema, *, method, strict, include_raw):
            self.structured_schema = schema
            self.structured_kwargs = {
                "method": method,
                "strict": strict,
                "include_raw": include_raw,
            }
            return self.structured

    monkeypatch.setattr(
        "src.infra.services.ai.langchain_openai_adapter.ChatOpenAI",
        FakeChatOpenAI,
    )
    return (
        OpenAILangChainAdapter(
            api_key="test-key",
            request_timeout_seconds=20,
            max_retries=1,
            store_responses=store_responses,
        ),
        created,
    )


def _adapter_with_parsing_error(monkeypatch, raw_content="raw model text"):
    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            pass

        def with_structured_output(self, schema, *, method, strict, include_raw):
            structured = MagicMock()
            structured.ainvoke = AsyncMock(
                return_value={
                    "raw": SimpleNamespace(content=raw_content),
                    "parsed": None,
                    "parsing_error": ValueError("invalid structured output"),
                }
            )
            return structured

    monkeypatch.setattr(
        "src.infra.services.ai.langchain_openai_adapter.ChatOpenAI",
        FakeChatOpenAI,
    )
    return OpenAILangChainAdapter(
        api_key="test-key",
        request_timeout_seconds=20,
        max_retries=1,
        store_responses=False,
    )


@pytest.mark.asyncio
async def test_structured_text_uses_chat_openai_with_responses_api(monkeypatch):
    adapter, created = _adapter(monkeypatch)

    result = await adapter.generate_structured(
        model="gpt-5.4-mini-2026-03-17",
        prompt="Chicken rice bowl",
        system_message="Return canonical JSON.",
        schema=VisionNutritionResponse,
        max_tokens=1500,
        request_kwargs={
            "prompt_cache_key": "mealtrack-test:parse_text:key",
            "prompt_cache_retention": "in_memory",
        },
    )

    assert isinstance(result, LangChainOpenAIResult)
    assert result.parsed.dish_name == "Chicken rice bowl"
    llm = created[0]
    assert llm.kwargs == {
        "model": "gpt-5.4-mini-2026-03-17",
        "api_key": "test-key",
        "timeout": 20,
        "max_retries": 1,
        "use_responses_api": True,
        "reasoning": {"effort": "none"},
    }
    assert llm.structured_schema["name"] == "VisionNutritionResponse"
    assert llm.structured_schema["strict"] is True
    assert llm.structured_schema["schema"]["type"] == "object"
    assert llm.structured_kwargs == {
        "method": "json_schema",
        "strict": True,
        "include_raw": True,
    }
    messages = llm.structured.ainvoke.await_args.args[0]
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)
    assert messages[0].content == "Return canonical JSON."
    assert messages[1].content == "Chicken rice bowl"
    assert llm.structured.ainvoke.await_args.kwargs == {
        "prompt_cache_key": "mealtrack-test:parse_text:key",
        "prompt_cache_retention": "in_memory",
        "max_tokens": 1500,
        "store": False,
    }


@pytest.mark.asyncio
async def test_raw_text_returns_raw_content_and_usage_metadata(monkeypatch):
    adapter, created = _adapter(monkeypatch, store_responses=True)

    result = await adapter.generate_raw(
        model="gpt-5.4-mini-2026-03-17",
        prompt="Generate summary.",
        system_message="Be concise.",
        max_tokens=None,
        request_kwargs={"prompt_cache_key": "mealtrack-test:general:key"},
    )

    assert result.parsed == {"raw_content": "raw answer"}
    llm = created[0]
    assert "max_tokens" not in llm.kwargs
    assert llm.ainvoke.await_args.kwargs == {
        "prompt_cache_key": "mealtrack-test:general:key",
        "store": True,
    }
    assert adapter.input_tokens(result.raw_message) == 1500.0
    assert adapter.cached_tokens(result.raw_message) == 512.0


@pytest.mark.asyncio
async def test_same_model_reuses_chat_openai_and_passes_max_tokens_per_call(
    monkeypatch,
):
    adapter, created = _adapter(monkeypatch)

    await adapter.generate_raw(
        model="gpt-5.4-mini-2026-03-17",
        prompt="First.",
        system_message="Be concise.",
        max_tokens=123,
        request_kwargs=None,
    )
    await adapter.generate_raw(
        model="gpt-5.4-mini-2026-03-17",
        prompt="Second.",
        system_message="Be concise.",
        max_tokens=456,
        request_kwargs=None,
    )

    assert len(created) == 1
    assert "max_tokens" not in created[0].kwargs
    assert created[0].ainvoke.await_count == 2
    assert created[0].ainvoke.await_args_list[0].kwargs == {
        "max_tokens": 123,
        "store": False,
    }
    assert created[0].ainvoke.await_args_list[1].kwargs == {
        "max_tokens": 456,
        "store": False,
    }


@pytest.mark.asyncio
async def test_vision_uses_multimodal_human_message_with_data_url(monkeypatch):
    adapter, created = _adapter(monkeypatch)

    result = await adapter.generate_vision_structured(
        model="gpt-5.4-mini-2026-03-17",
        prompt="Identify food.",
        image_data=b"image-bytes",
        image_mime_type="image/png",
        system_message=None,
        schema=VisionNutritionResponse,
        max_tokens=1200,
        request_kwargs={"prompt_cache_key": "mealtrack-test:meal_scan:key"},
    )

    assert result.parsed.foods[0].name == "grilled chicken"
    messages = created[0].structured.ainvoke.await_args.args[0]
    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content == ""
    assert isinstance(messages[1], HumanMessage)
    assert messages[1].content[0] == {"type": "text", "text": "Identify food."}
    assert messages[1].content[1]["type"] == "image_url"
    assert (
        messages[1].content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    )
    assert messages[1].content[1]["image_url"]["url"].endswith("aW1hZ2UtYnl0ZXM=")
    assert messages[1].content[1]["image_url"]["detail"] == "high"
    assert created[0].structured.ainvoke.await_args.kwargs == {
        "prompt_cache_key": "mealtrack-test:meal_scan:key",
        "max_tokens": 1200,
        "store": False,
    }
    assert created[0].structured_schema["name"] == "VisionNutritionResponse"
    assert created[0].structured_schema["strict"] is True


def test_usage_extractors_support_response_metadata_fallback(monkeypatch):
    adapter, _ = _adapter(monkeypatch)
    message = SimpleNamespace(
        content="ok",
        usage_metadata=None,
        response_metadata={
            "token_usage": {
                "prompt_tokens": 222,
                "prompt_tokens_details": {"cached_tokens": 111},
            }
        },
    )

    assert adapter.input_tokens(message) == 222.0
    assert adapter.cached_tokens(message) == 111.0


def test_usage_extractors_return_zero_when_missing(monkeypatch):
    adapter, _ = _adapter(monkeypatch)
    message = SimpleNamespace(content="ok")

    assert adapter.input_tokens(message) == 0.0
    assert adapter.cached_tokens(message) == 0.0


@pytest.mark.asyncio
async def test_structured_text_raises_parsing_error(monkeypatch):
    adapter = _adapter_with_parsing_error(monkeypatch)

    with pytest.raises(ValueError, match="invalid structured output") as error:
        await adapter.generate_structured(
            model="gpt-5.4-mini-2026-03-17",
            prompt="Chicken rice bowl",
            system_message="Return canonical JSON.",
            schema=VisionNutritionResponse,
            max_tokens=None,
            request_kwargs={},
        )

    assert error.value.parse_text_candidate_output == "raw model text"


@pytest.mark.asyncio
async def test_structured_text_captures_responses_api_content_blocks(monkeypatch):
    adapter = _adapter_with_parsing_error(
        monkeypatch,
        raw_content=[
            {"type": "text", "text": '{"items":[{"name":"Granola"}]}'},
            {"type": "refusal", "refusal": "not included"},
        ],
    )

    with pytest.raises(ValueError) as error:
        await adapter.generate_structured(
            model="gpt-5.4-mini-2026-03-17",
            prompt="10g granola",
            system_message="Return canonical JSON.",
            schema=VisionNutritionResponse,
            max_tokens=None,
            request_kwargs={},
        )

    assert error.value.parse_text_candidate_output == '{"items":[{"name":"Granola"}]}'


@pytest.mark.asyncio
async def test_structured_schema_is_openai_subset_compatible(monkeypatch):
    adapter, created = _adapter(monkeypatch)

    await adapter.generate_vision_structured(
        model="gpt-5.4-mini-2026-03-17",
        prompt="Identify food.",
        image_data=b"image-bytes",
        image_mime_type="image/jpeg",
        system_message="Return canonical JSON.",
        schema=VisionNutritionResponse,
        max_tokens=None,
        request_kwargs=None,
    )

    schema = created[0].structured_schema["schema"]
    unsupported_keys = {
        "default",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "title",
    }

    assert not _schema_contains_any_key(schema, unsupported_keys)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    for definition in schema["$defs"].values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False
            assert set(definition["required"]) == set(definition["properties"])


def _schema_contains_any_key(value, keys: set[str]) -> bool:
    if isinstance(value, dict):
        return any(key in keys for key in value) or any(
            _schema_contains_any_key(child, keys) for child in value.values()
        )
    if isinstance(value, list):
        return any(_schema_contains_any_key(item, keys) for item in value)
    return False
