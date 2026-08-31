"""OpenAI implementation of AIProviderPort using LangChain OpenAI adapter."""

from __future__ import annotations

import re
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)
from pydantic import ValidationError

from src.domain.ports.ai_provider_port import AICapability, AIProviderPort
from src.domain.services.ai_output_validation_service import summarize_validation_error
from src.infra.adapters.ai_json_utils import extract_json as extract_ai_json
from src.infra.services.ai.ai_vision_errors import AIVisionError, AIVisionFailureKind
from src.infra.services.ai.langchain_openai_adapter import OpenAILangChainAdapter
from src.infra.services.ai.openai_prompt_cache_policy import OpenAIPromptCachePolicy
from src.infra.services.ai.openai_structured_generation_result import (
    OpenAIStructuredGenerationResult,
)
from src.observability import increment_metric


class OpenAIProvider(AIProviderPort):
    """OpenAI provider for text, vision, and structured output."""

    def __init__(
        self,
        *,
        api_key: str,
        request_timeout_seconds: int,
        max_retries: int,
        store_responses: bool,
        prompt_cache_enabled: bool = True,
        prompt_cache_retention: str | None = None,
        prompt_cache_key_prefix: str = "mealtrack",
    ) -> None:
        self._langchain = OpenAILangChainAdapter(
            api_key=api_key,
            request_timeout_seconds=request_timeout_seconds,
            max_retries=max_retries,
            store_responses=store_responses,
        )
        self._prompt_cache_policy = OpenAIPromptCachePolicy(
            enabled=prompt_cache_enabled,
            key_prefix=prompt_cache_key_prefix,
            retention=prompt_cache_retention,
        )

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def supported_capabilities(self) -> set[AICapability]:
        return {
            AICapability.TEXT_GENERATION,
            AICapability.VISION,
            AICapability.STRUCTURED_OUTPUT,
        }

    def get_available_models(self) -> list[str]:
        return []

    def _prompt_cache_kwargs(
        self,
        *,
        model: str,
        purpose_hint: str | None,
        system_message: str | None,
    ) -> dict[str, Any]:
        return self._prompt_cache_policy.request_kwargs(
            model=model,
            purpose_hint=purpose_hint,
            system_message=system_message,
        )

    def _record_prompt_cache_usage(
        self,
        raw_message: Any,
        *,
        model: str,
        purpose_hint: str | None,
    ) -> None:
        input_tokens = self._langchain.input_tokens(raw_message)
        cached_tokens = self._langchain.cached_tokens(raw_message)
        attributes = {
            "ai_provider": "openai",
            "ai_model": model,
            "ai_purpose": purpose_hint or "unknown",
            "cache_hit": "true" if cached_tokens > 0 else "false",
        }
        increment_metric(
            "ai.openai.prompt_cache.request.count",
            attributes=attributes,
        )
        increment_metric(
            "ai.openai.prompt_cache.cached_tokens",
            cached_tokens,
            unit="token",
            attributes=attributes,
        )
        if input_tokens > 0:
            increment_metric(
                "ai.openai.prompt_cache.input_tokens",
                input_tokens,
                unit="token",
                attributes=attributes,
            )

    async def generate(
        self,
        model: str,
        prompt: str,
        system_message: str,
        response_type: str = "json",
        max_tokens: int | None = None,
        schema: type | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        purpose_hint = kwargs.get("purpose_hint")
        prompt_cache_kwargs = self._prompt_cache_kwargs(
            model=model,
            purpose_hint=purpose_hint,
            system_message=system_message,
        )
        if schema is not None:
            result = await self._langchain.generate_structured(
                model=model,
                prompt=prompt,
                system_message=system_message,
                schema=schema,
                max_tokens=max_tokens,
                request_kwargs=prompt_cache_kwargs,
            )
            self._record_prompt_cache_usage(
                result.raw_message,
                model=model,
                purpose_hint=purpose_hint,
            )
            return self._dump_parsed(result.parsed)

        result = await self._langchain.generate_raw(
            model=model,
            prompt=prompt,
            system_message=system_message,
            max_tokens=max_tokens,
            request_kwargs=prompt_cache_kwargs,
        )
        self._record_prompt_cache_usage(
            result.raw_message,
            model=model,
            purpose_hint=purpose_hint,
        )
        if response_type == "json":
            raw_content = result.parsed.get("raw_content", "")
            return extract_ai_json(raw_content)
        return self._dump_parsed(result.parsed)

    async def generate_structured_result(
        self,
        model: str,
        prompt: str,
        system_message: str,
        *,
        schema: type,
        max_tokens: int | None = None,
        store_responses: bool | None = None,
        **kwargs: Any,
    ) -> OpenAIStructuredGenerationResult:
        """Return parsed structured data plus sanitized response metadata."""
        purpose_hint = kwargs.get("purpose_hint")
        prompt_cache_kwargs = self._prompt_cache_kwargs(
            model=model,
            purpose_hint=purpose_hint,
            system_message=system_message,
        )
        result = await self._langchain.generate_structured(
            model=model,
            prompt=prompt,
            system_message=system_message,
            schema=schema,
            max_tokens=max_tokens,
            request_kwargs=prompt_cache_kwargs,
            store_override=store_responses,
        )
        self._record_prompt_cache_usage(
            result.raw_message,
            model=model,
            purpose_hint=purpose_hint,
        )
        raw = result.raw_message
        response_metadata = getattr(raw, "response_metadata", {})
        refusal = bool(_metadata_value(response_metadata, "refusal"))
        incomplete = _response_is_incomplete(response_metadata)
        return OpenAIStructuredGenerationResult(
            parsed=result.parsed,
            raw_message=raw,
            refusal=refusal,
            incomplete=incomplete,
            usage=_safe_usage(raw),
        )

    async def generate_with_vision(
        self,
        model: str,
        prompt: str,
        image_data: bytes,
        system_message: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        schema = kwargs["schema"]
        max_tokens: int | None = kwargs.get("max_tokens")
        image_mime_type = kwargs.get("image_mime_type", "image/jpeg")
        purpose_hint = kwargs.get("purpose_hint")
        prompt_cache_kwargs = self._prompt_cache_kwargs(
            model=model,
            purpose_hint=purpose_hint,
            system_message=system_message,
        )

        try:
            result = await self._langchain.generate_vision_structured(
                model=model,
                prompt=prompt,
                image_data=image_data,
                image_mime_type=image_mime_type,
                system_message=system_message,
                schema=schema,
                max_tokens=max_tokens,
                request_kwargs=prompt_cache_kwargs,
                image_detail=kwargs.get("image_detail", "high"),
            )
        except ValidationError as exc:
            raise AIVisionError(
                f"[OPENAI-VISION-SCHEMA-FAIL] provider=openai model={model}",
                kind=AIVisionFailureKind.schema_validation,
                provider="openai",
                model=model,
                validation_details=summarize_validation_error(exc),
            ) from exc
        self._record_prompt_cache_usage(
            result.raw_message,
            model=model,
            purpose_hint=purpose_hint,
        )
        return self._dump_parsed(result.parsed)

    def extract_error_code(self, error: Exception) -> int | str | None:
        if isinstance(error, RateLimitError):
            return 429
        if isinstance(error, APITimeoutError):
            return "timeout"
        if isinstance(error, APIConnectionError):
            return "connection"
        if isinstance(error, APIStatusError):
            return error.status_code

        match = re.search(r"\b(429|500|502|503|504)\b", str(error))
        if match:
            return int(match.group(1))
        return None

    def _dump_parsed(self, parsed: Any) -> dict[str, Any]:
        if hasattr(parsed, "model_dump"):
            return parsed.model_dump()
        return dict(parsed)


def _metadata_value(metadata: Any, key: str) -> Any:
    if isinstance(metadata, dict):
        return metadata.get(key)
    return getattr(metadata, key, None)


def _response_is_incomplete(metadata: Any) -> bool:
    """Normalize Responses API completion metadata across LangChain versions."""
    if bool(_metadata_value(metadata, "incomplete")):
        return True
    status = _metadata_value(metadata, "status")
    if isinstance(status, str) and status.lower() != "completed":
        return True
    details = _metadata_value(metadata, "incomplete_details")
    return details not in (None, {}, "")


def _safe_usage(message: Any) -> dict[str, int]:
    usage = getattr(message, "usage_metadata", None)
    if not isinstance(usage, dict):
        return {}
    result: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and value >= 0:
            result[key] = value
    return result
