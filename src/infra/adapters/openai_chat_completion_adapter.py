"""OpenAI Responses API streaming adapter for the chat coach."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.domain.model.chat import (
    CHAT_INTENTS,
    ChatCompletionDelta,
    ChatHistoryTurn,
    ChatMessageRole,
    ChatUsage,
)
from src.domain.ports.chat_completion_port import ChatCompletionPort
from src.domain.ports.chat_follow_up_port import ChatFollowUpPort
from src.domain.services.chat.follow_up_schema import (
    ChatFollowUpList,
    sanitize_follow_ups,
)

FOLLOW_UP_TIMEOUT_SECONDS = 2.0
FOLLOW_UP_MAX_TOKENS = 180


class OpenAIChatCompletionAdapter(ChatCompletionPort, ChatFollowUpPort):
    """Stateless streaming completion with store=false."""

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: int,
        max_retries: int = 0,
        reasoning_effort: str = "low",
    ) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._reasoning_effort = reasoning_effort
        self._llms: dict[str, ChatOpenAI] = {}
        self._structured_llms: dict[str, ChatOpenAI] = {}

    def _llm(self, model: str) -> ChatOpenAI:
        cached = self._llms.get(model)
        if cached is not None:
            return cached
        llm = ChatOpenAI(
            model=model,
            api_key=self._api_key,
            timeout=self._timeout_seconds,
            max_retries=self._max_retries,
            use_responses_api=True,
            reasoning={"effort": self._reasoning_effort},
            streaming=True,
        )
        self._llms[model] = llm
        return llm

    async def stream(
        self,
        *,
        model: str,
        system_instructions: str,
        grounding_message: str,
        history: list[ChatHistoryTurn],
        user_message: str,
        max_output_tokens: int,
        cache_kwargs: dict[str, Any] | None = None,
    ) -> AsyncIterator[ChatCompletionDelta]:
        messages: list[Any] = [
            SystemMessage(content=system_instructions),
            HumanMessage(content=grounding_message),
        ]
        for turn in history:
            if turn.role == ChatMessageRole.USER:
                messages.append(HumanMessage(content=turn.content))
            else:
                messages.append(AIMessage(content=turn.content))
        messages.append(HumanMessage(content=user_message))

        invocation = dict(cache_kwargs or {})
        invocation["max_tokens"] = max_output_tokens
        invocation["store"] = False

        llm = self._llm(model)
        provider_response_id: str | None = None
        usage = ChatUsage(model=model)
        async for chunk in llm.astream(messages, **invocation):
            text = _chunk_text(chunk)
            provider_response_id = _response_id(chunk) or provider_response_id
            chunk_usage = _chunk_usage(chunk, model)
            if chunk_usage is not None:
                usage = chunk_usage
            if text:
                yield ChatCompletionDelta(
                    text=text,
                    provider_response_id=provider_response_id,
                )
        yield ChatCompletionDelta(
            text="",
            provider_response_id=provider_response_id,
            usage=usage,
            done=True,
        )

    def _structured_llm(self, model: str) -> ChatOpenAI:
        cached = self._structured_llms.get(model)
        if cached is not None:
            return cached
        llm = ChatOpenAI(
            model=model,
            api_key=self._api_key,
            timeout=min(self._timeout_seconds, 8),
            max_retries=0,
            use_responses_api=True,
            reasoning={"effort": "low"},
            streaming=False,
        )
        self._structured_llms[model] = llm
        return llm

    async def generate_follow_ups(
        self,
        *,
        model: str,
        locale: str,
        intent: str | None,
        slot: str | None,
        user_message: str,
        assistant_text: str,
        has_suggestions: bool,
    ) -> list[dict[str, str]]:
        prompt = json.dumps(
            {
                "locale": locale,
                "intent": intent,
                "suggested_meal_slot": slot,
                "user_message": user_message[:400],
                "assistant_text": assistant_text[:800],
                "has_suggestions": has_suggestions,
                "allowed_actions": list(CHAT_INTENTS),
            },
            ensure_ascii=False,
        )
        structured = self._structured_llm(model).with_structured_output(
            ChatFollowUpList,
            method="json_schema",
            strict=True,
        )
        try:
            parsed = await asyncio.wait_for(
                structured.ainvoke(
                    [
                        SystemMessage(content=_FOLLOW_UP_INSTRUCTIONS),
                        HumanMessage(content=prompt),
                    ],
                    store=False,
                    max_tokens=FOLLOW_UP_MAX_TOKENS,
                ),
                timeout=FOLLOW_UP_TIMEOUT_SECONDS,
            )
        except Exception:
            return []
        return sanitize_follow_ups(parsed)


_FOLLOW_UP_INSTRUCTIONS = """Author 2-3 short Nutree Coach follow-up chips.
Each chip is {label, action}. action must be one of the allowed_actions.
Reply in the given locale. Labels are tap targets, not answers.
Do not invent nutrition numbers. Do not claim a meal was saved or logged.
If has_suggestions is true, one chip may request more ideas via next_meal.
"""


def _chunk_text(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def _response_id(chunk: Any) -> str | None:
    additional = getattr(chunk, "additional_kwargs", None) or {}
    if isinstance(additional, dict):
        for key in ("id", "response_id"):
            value = additional.get(key)
            if isinstance(value, str) and value:
                return value
    response_metadata = getattr(chunk, "response_metadata", None) or {}
    if isinstance(response_metadata, dict):
        value = response_metadata.get("id") or response_metadata.get("response_id")
        if isinstance(value, str) and value:
            return value
    ident = getattr(chunk, "id", None)
    return ident if isinstance(ident, str) else None


def _chunk_usage(chunk: Any, model: str) -> ChatUsage | None:
    metadata = getattr(chunk, "usage_metadata", None) or {}
    response_metadata = getattr(chunk, "response_metadata", None) or {}
    token_usage = {}
    if isinstance(response_metadata, dict):
        token_usage = response_metadata.get("token_usage") or {}
    if not metadata and not token_usage:
        return None
    input_tokens = int(
        metadata.get("input_tokens")
        or token_usage.get("prompt_tokens")
        or token_usage.get("input_tokens")
        or 0
    )
    output_tokens = int(
        metadata.get("output_tokens")
        or token_usage.get("completion_tokens")
        or token_usage.get("output_tokens")
        or 0
    )
    cached_tokens = int(
        metadata.get("input_token_details", {}).get("cache_read")
        or token_usage.get("cached_tokens")
        or 0
    )
    if input_tokens == 0 and output_tokens == 0 and cached_tokens == 0:
        return None
    return ChatUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        model=model,
    )
