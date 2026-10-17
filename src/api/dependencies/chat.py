"""Composition root for the single-thread chat coach."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from src.app.services.chat_context_builder import ChatContextBuilder
from src.app.services.chat_next_meal_candidates import (
    ChatNextMealCandidates,
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

logger = logging.getLogger(__name__)


@lru_cache
def get_chat_turn_orchestrator() -> ChatTurnOrchestrator:
    api_key = settings.OPENAI_API_KEY or ""
    cache_policy = OpenAIPromptCachePolicy(
        enabled=settings.OPENAI_PROMPT_CACHE_ENABLED and bool(api_key),
        key_prefix=settings.OPENAI_PROMPT_CACHE_KEY_PREFIX,
        retention=settings.OPENAI_PROMPT_CACHE_RETENTION or None,
    )
    from src.api.base_dependencies import get_cache_service

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

    next_meals = _next_meal_service(
        recipe_generator=adapter if api_key else None,
        model=settings.CHAT_MODEL,
    )

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
        next_meals=next_meals,
        follow_ups=follow_ups,
    )


def _next_meal_service(
    *,
    recipe_generator: Any = None,
    model: str = "gpt-5.6-luna",
) -> ChatNextMealCandidates:
    from src.api.base_dependencies import get_nutrition_lookup_service
    from src.api.dependencies.food_image import get_food_image_service

    return ChatNextMealCandidates(
        discover=None,
        recipe_generator=recipe_generator,
        image_search=get_food_image_service().search_food_image,
        image_generator=_chat_meal_image_generator(),
        nutrition_lookup=get_nutrition_lookup_service(),
        model=model,
    )


def _chat_meal_image_generator():
    """Cloudflare Workers AI photo for a chat card. Stock search remains the fallback."""
    from src.api.base_dependencies import get_image_store
    from src.app.services.chat_meal_image import (
        CHAT_MEAL_IMAGE_TIMEOUT_SECONDS,
        ChatMealImageGenerator,
    )
    from src.infra.adapters.cloudflare_workers_image_generator import (
        CloudflareWorkersImageGenerator,
    )
    from src.infra.adapters.image_url_probe import image_url_exists

    account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    api_token = (settings.CLOUDFLARE_API_TOKEN or "").strip()
    if not account_id or not api_token:
        logger.info("chat meal photos use stock search; Cloudflare is not configured")
        return None

    async def existing_image(image_id: str) -> str | None:
        url = get_image_store().get_url(image_id)
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            return None
        if await image_url_exists(url):
            return url
        return None

    generator = CloudflareWorkersImageGenerator(
        account_id=account_id,
        api_token=api_token,
        model=settings.CLOUDFLARE_WORKERS_AI_IMAGE_MODEL,
        timeout=CHAT_MEAL_IMAGE_TIMEOUT_SECONDS,
        image_store=get_image_store(),
    )
    return ChatMealImageGenerator(generator, existing_image=existing_image)


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
