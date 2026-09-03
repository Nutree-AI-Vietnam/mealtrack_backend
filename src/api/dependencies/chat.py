"""Composition root for the single-thread chat coach."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from src.app.services.chat_context_builder import ChatContextBuilder
from src.app.services.chat_next_meal_candidates import (
    ChatNextMealCandidates,
    SuggestionChatDiscoverAdapter,
)
from src.app.services.chat_turn_orchestrator import ChatTurnOrchestrator
from src.domain.exceptions.chat_exceptions import ChatProviderUnavailableError
from src.domain.model.chat import ChatCompletionDelta, ChatHistoryTurn
from src.domain.ports.chat_completion_port import ChatCompletionPort
from src.domain.ports.chat_embedding_port import ChatEmbeddingPort
from src.infra.adapters.chat_knowledge_retrieval_adapter import (
    ChatKnowledgeRetrievalAdapter,
)
from src.infra.adapters.openai_chat_completion_adapter import (
    OpenAIChatCompletionAdapter,
)
from src.infra.adapters.openai_chat_embedding_adapter import OpenAIChatEmbeddingAdapter
from src.infra.config.settings import settings
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.services.ai.openai_prompt_cache_policy import OpenAIPromptCachePolicy
from src.infra.services.chat_concurrency import (
    get_chat_circuit_breaker,
    get_chat_semaphore,
)


@lru_cache
def get_chat_turn_orchestrator() -> ChatTurnOrchestrator:
    api_key = settings.OPENAI_API_KEY or ""
    cache_policy = OpenAIPromptCachePolicy(
        enabled=settings.OPENAI_PROMPT_CACHE_ENABLED and bool(api_key),
        key_prefix=settings.OPENAI_PROMPT_CACHE_KEY_PREFIX,
        retention=settings.OPENAI_PROMPT_CACHE_RETENTION or None,
    )
    from src.api.base_dependencies import (
        get_cache_service,
        get_suggestion_orchestration_service,
    )
    from src.api.dependencies.food_image import get_food_image_service

    completion: ChatCompletionPort
    embedding: ChatEmbeddingPort
    follow_ups = None
    if api_key:
        adapter = OpenAIChatCompletionAdapter(
            api_key=api_key,
            timeout_seconds=settings.CHAT_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
            reasoning_effort=settings.CHAT_REASONING_EFFORT,
        )
        completion = adapter
        follow_ups = adapter
        embedding = OpenAIChatEmbeddingAdapter(
            api_key=api_key,
            model=settings.CHAT_EMBEDDING_MODEL,
        )
    else:
        completion = _UnavailableCompletion()
        embedding = _UnavailableEmbedding()

    return ChatTurnOrchestrator(
        completion=completion,
        embedding=embedding,
        retrieval=ChatKnowledgeRetrievalAdapter(AsyncUnitOfWork),
        context_builder=ChatContextBuilder(
            uow_factory=AsyncUnitOfWork,
            cache_service=get_cache_service(),
        ),
        uow_factory=AsyncUnitOfWork,
        model=settings.CHAT_MODEL,
        daily_turn_budget=settings.CHAT_DAILY_TURN_BUDGET,
        generation_lease_seconds=settings.CHAT_GENERATION_LEASE_SECONDS,
        global_concurrency=settings.CHAT_GLOBAL_CONCURRENCY,
        cache_policy=cache_policy,
        max_output_tokens=settings.CHAT_MAX_OUTPUT_TOKENS,
        semaphore=get_chat_semaphore(settings.CHAT_GLOBAL_CONCURRENCY),
        circuit_breaker=get_chat_circuit_breaker(),
        next_meals=ChatNextMealCandidates(
            SuggestionChatDiscoverAdapter(
                get_suggestion_orchestration_service(),
                image_search=get_food_image_service().search_food_image,
            )
        ),
        follow_ups=follow_ups,
    )


class _UnavailableCompletion(ChatCompletionPort):
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
        del (
            model,
            system_instructions,
            grounding_message,
            history,
            user_message,
            max_output_tokens,
            cache_kwargs,
        )
        if False:  # pragma: no cover - makes this an async generator
            yield ChatCompletionDelta(text="")
        raise ChatProviderUnavailableError(retry_after_seconds=30, retryable=True)


class _UnavailableEmbedding(ChatEmbeddingPort):
    async def embed_query(self, text: str) -> list[float]:
        del text
        return []
