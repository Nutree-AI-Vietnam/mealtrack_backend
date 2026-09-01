"""Composition root for the single-thread chat coach."""

from __future__ import annotations

from functools import lru_cache

from src.app.services.chat_context_builder import ChatContextBuilder
from src.app.services.chat_turn_orchestrator import ChatTurnOrchestrator
from src.domain.exceptions.chat_exceptions import ChatProviderUnavailableError
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
    from src.api.base_dependencies import get_cache_service

    if api_key:
        completion = OpenAIChatCompletionAdapter(
            api_key=api_key,
            timeout_seconds=settings.CHAT_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
            reasoning_effort=settings.CHAT_REASONING_EFFORT,
        )
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
    )


class _UnavailableCompletion:
    async def stream(self, **kwargs):
        if False:  # pragma: no cover - makes this an async generator
            yield None
        raise ChatProviderUnavailableError(retry_after_seconds=30, retryable=True)


class _UnavailableEmbedding:
    async def embed_query(self, text: str) -> list[float]:
        del text
        return []
