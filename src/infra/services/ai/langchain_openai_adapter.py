"""LangChain OpenAI adapter for text, structured, and vision calls."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel


@dataclass(frozen=True)
class LangChainOpenAIResult:
    parsed: Any
    raw_message: Any


class OpenAILangChainAdapter:
    """Thin infrastructure wrapper around LangChain ChatOpenAI."""

    def __init__(
        self,
        *,
        api_key: str,
        request_timeout_seconds: int,
        max_retries: int,
        store_responses: bool,
    ) -> None:
        self._api_key = api_key
        self._request_timeout_seconds = request_timeout_seconds
        self._max_retries = max_retries
        self._store_responses = store_responses
        self._llms: dict[str, ChatOpenAI] = {}

    async def generate_structured(
        self,
        *,
        model: str,
        prompt: str,
        system_message: str,
        schema: type,
        max_tokens: int | None,
        request_kwargs: dict[str, Any] | None,
        store_override: bool | None = None,
    ) -> LangChainOpenAIResult:
        llm = self._llm(model=model)
        structured = llm.with_structured_output(
            _openai_json_schema(schema),
            method="json_schema",
            strict=True,
            include_raw=True,
        )
        response = await structured.ainvoke(
            [
                SystemMessage(content=system_message),
                HumanMessage(content=prompt),
            ],
            **self._request_kwargs(
                request_kwargs, max_tokens=max_tokens, store_override=store_override
            ),
        )
        parsing_error = response.get("parsing_error")
        if parsing_error is not None:
            raise parsing_error
        return LangChainOpenAIResult(
            parsed=_validate_parsed(schema, response["parsed"]),
            raw_message=response["raw"],
        )

    async def generate_raw(
        self,
        *,
        model: str,
        prompt: str,
        system_message: str,
        max_tokens: int | None,
        request_kwargs: dict[str, Any] | None,
    ) -> LangChainOpenAIResult:
        llm = self._llm(model=model)
        raw_message = await llm.ainvoke(
            [
                SystemMessage(content=system_message),
                HumanMessage(content=prompt),
            ],
            **self._request_kwargs(request_kwargs, max_tokens=max_tokens),
        )
        return LangChainOpenAIResult(
            parsed={"raw_content": self.text(raw_message)},
            raw_message=raw_message,
        )

    async def generate_vision_structured(
        self,
        *,
        model: str,
        prompt: str,
        image_data: bytes,
        image_mime_type: str,
        system_message: str | None,
        schema: type,
        max_tokens: int | None,
        request_kwargs: dict[str, Any] | None,
        image_detail: str = "high",
    ) -> LangChainOpenAIResult:
        llm = self._llm(model=model)
        structured = llm.with_structured_output(
            _openai_json_schema(schema),
            method="json_schema",
            strict=True,
            include_raw=True,
        )
        image_b64 = base64.b64encode(image_data).decode("ascii")
        data_url = f"data:{image_mime_type};base64,{image_b64}"
        detail_tier = (
            image_detail if image_detail in {"low", "high", "auto"} else "high"
        )
        response = await structured.ainvoke(
            [
                SystemMessage(content=system_message or ""),
                HumanMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url, "detail": detail_tier},
                        },
                    ]
                ),
            ],
            **self._request_kwargs(request_kwargs, max_tokens=max_tokens),
        )
        parsing_error = response.get("parsing_error")
        if parsing_error is not None:
            raise parsing_error
        return LangChainOpenAIResult(
            parsed=_validate_parsed(schema, response["parsed"]),
            raw_message=response["raw"],
        )

    def _llm(self, *, model: str) -> ChatOpenAI:
        if model in self._llms:
            return self._llms[model]
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": self._api_key,
            "timeout": self._request_timeout_seconds,
            "max_retries": self._max_retries,
            "use_responses_api": True,
            "reasoning": {"effort": "none"},
        }
        self._llms[model] = ChatOpenAI(**kwargs)
        return self._llms[model]

    def _request_kwargs(
        self,
        request_kwargs: dict[str, Any] | None,
        *,
        max_tokens: int | None,
        store_override: bool | None = None,
    ) -> dict[str, Any]:
        invocation_kwargs = dict(request_kwargs or {})
        if max_tokens is not None:
            invocation_kwargs["max_tokens"] = max_tokens
        invocation_kwargs["store"] = (
            self._store_responses if store_override is None else store_override
        )
        return invocation_kwargs

    @staticmethod
    def text(message: Any) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            return "".join(parts)
        if content is None:
            return ""
        return str(content)

    @staticmethod
    def input_tokens(message: Any) -> float:
        usage_metadata = getattr(message, "usage_metadata", None)
        value = _mapping_get(usage_metadata, "input_tokens")
        if value is not None:
            return _number(value)

        response_metadata = getattr(message, "response_metadata", None)
        token_usage = _mapping_get(response_metadata, "token_usage")
        return _number(_mapping_get(token_usage, "prompt_tokens"))

    @staticmethod
    def cached_tokens(message: Any) -> float:
        usage_metadata = getattr(message, "usage_metadata", None)
        input_details = _mapping_get(usage_metadata, "input_token_details")
        value = _mapping_get(input_details, "cache_read")
        if value is not None:
            return _number(value)

        response_metadata = getattr(message, "response_metadata", None)
        token_usage = _mapping_get(response_metadata, "token_usage")
        prompt_details = _mapping_get(token_usage, "prompt_tokens_details")
        return _number(_mapping_get(prompt_details, "cached_tokens"))


def _mapping_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _number(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _openai_json_schema(schema: type) -> dict[str, Any]:
    if not isinstance(schema, type) or not issubclass(schema, BaseModel):
        raise TypeError("OpenAI structured output schema must be a Pydantic model")
    return {
        "name": schema.__name__,
        "schema": _normalize_for_openai_structured_outputs(schema.model_json_schema()),
        "strict": True,
    }


def _validate_parsed(schema: type, parsed: Any) -> BaseModel:
    if isinstance(parsed, schema):
        return parsed
    return schema.model_validate(parsed)


def _normalize_for_openai_structured_outputs(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_for_openai_structured_outputs(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized: dict[str, Any] = {}
    for key, child in value.items():
        if key in _OPENAI_STRUCTURED_OUTPUT_UNSUPPORTED_KEYS:
            continue
        normalized[key] = _normalize_for_openai_structured_outputs(child)

    if normalized.get("type") == "object":
        properties = normalized.get("properties")
        if isinstance(properties, dict):
            normalized["required"] = list(properties.keys())
        normalized["additionalProperties"] = False

    return normalized


_OPENAI_STRUCTURED_OUTPUT_UNSUPPORTED_KEYS = {
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
