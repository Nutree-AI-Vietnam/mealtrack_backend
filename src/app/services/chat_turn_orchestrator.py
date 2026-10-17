"""Chat turn orchestration: claim, ground, generate, validate, persist, SSE events."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from src.app.services.chat_next_meal_candidates import (
    ChatNextMealCandidates,
    last_discover_session_id,
)
from src.domain.exceptions.chat_exceptions import (
    ChatInvalidCursorError,
    ChatProviderUnavailableError,
    ChatRateLimitedError,
    ChatRegenerateNotAllowedError,
    ChatThreadNotFoundError,
)
from src.domain.model.chat import (
    CHAT_CONTEXT_VERSION,
    CHAT_HISTORY_LIMIT,
    CHAT_MAX_OUTPUT_TOKENS,
    CHAT_MAX_USER_MESSAGE_CHARS,
    CHAT_OUTPUT_TOKEN_CEILING,
    CHAT_PROMPT_VERSION,
    CHAT_RETRIEVAL_MAX_CHUNKS,
    ChatCitation,
    ChatClaimKind,
    ChatHistoryTurn,
    ChatIntent,
    ChatMessage,
    ChatMessageRole,
    ChatSseEvent,
    ChatThread,
    ChatThreadListing,
    ChatTurnClaim,
    ChatUsage,
    ChatUserContext,
    RetrievedKnowledgeChunk,
    chronological_chat_messages,
    empty_reply_payload,
    reply_sidecar,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.chat_completion_port import ChatCompletionPort
from src.domain.ports.chat_embedding_port import ChatEmbeddingPort
from src.domain.ports.chat_follow_up_port import ChatFollowUpPort
from src.domain.ports.chat_knowledge_retrieval_port import ChatKnowledgeRetrievalPort
from src.domain.ports.chat_repository_port import ChatRepositoryPort
from src.domain.services.chat.coach_functions import (
    FUNCTION_CHECK_DAILY_PROGRESS,
    FUNCTION_EXPLAIN_LIMITS,
    FUNCTION_SEARCH_KNOWLEDGE,
    FUNCTION_SUGGEST_NEXT_MEAL,
    needs_retrieval,
    openai_tools,
    preferred_alias,
    resolve_coach_function,
)
from src.domain.services.chat.follow_up_schema import ensure_follow_ups, meal_names
from src.domain.services.chat.meal_request import (
    is_cook_request,
    is_ingredient_request,
    is_meal_comparison,
    is_meal_explanation,
    is_meal_refinement,
    is_meal_suggestion_request,
)
from src.domain.services.chat.meal_slot import resolve_meal_slot, slot_from_user_text
from src.domain.services.chat.memory import CHAT_MEMORY_FETCH, rolling_memory
from src.domain.services.chat.next_meal_intro import (
    cook_reply_already_said,
    eaten_today_name,
    next_meal_ack,
    next_meal_intro,
    next_meal_unavailable,
    verified_cook_reply,
    verified_ingredient_reply,
    with_eaten_today,
)
from src.domain.services.chat.policy import (
    SentenceBuffer,
    build_grounding_message,
    citations_are_valid,
    cited_labels,
    filter_chunks_for_allergies,
    finished_assistant_text,
    hydrate_citations,
    inspect_sentence,
    meal_reply_is_usable,
    no_evidence_message,
    nutrition_numbers_are_traceable,
    request_fingerprint,
    resolve_chat_locale,
    safe_fallback_message,
    sanitize_incomplete_assistant_text,
    stable_system_instructions,
)
from src.domain.services.chat.thread_listing import (
    decode_thread_cursor,
    encode_thread_cursor,
)
from src.domain.services.chat.welcome import coach_welcome
from src.domain.utils.timezone_utils import utc_now
from src.observability import distribution_metric, increment_metric, log_event

logger = logging.getLogger(__name__)

_RETRYABLE_PROVIDER_MARKERS = (
    "timeout",
    "temporarily",
    "unavailable",
    "429",
    "500",
    "502",
    "503",
    "504",
)


class PromptCachePolicy(Protocol):
    def request_kwargs(
        self,
        *,
        model: str,
        purpose_hint: str | None,
        system_message: str | None,
    ) -> dict[str, Any]: ...


class CircuitBreaker(Protocol):
    def get_state(self, model: str) -> Any: ...

    def record_success(self, model: str) -> None: ...

    def record_failure(self, model: str) -> None: ...


@dataclass(slots=True)
class PreparedChatTurn:
    claim: ChatTurnClaim
    content: str
    locale: str
    header_timezone: str | None
    started: float
    intent: str | None = None
    function_name: str | None = None
    function_args: dict[str, Any] | None = None
    slot_acquired: bool = False


CHAT_ORCHESTRATOR_TOOLS: list[dict[str, Any]] = openai_tools()


def _merge_retrieved_chunks(
    existing_chunks: list[RetrievedKnowledgeChunk],
    new_chunks: list[RetrievedKnowledgeChunk],
) -> list[RetrievedKnowledgeChunk]:
    merged = list(existing_chunks)
    existing_keys = {c.source_key for c in existing_chunks}
    for chunk in new_chunks:
        if chunk.source_key not in existing_keys:
            new_label = f"[K{len(merged) + 1}]"
            labeled_chunk = RetrievedKnowledgeChunk(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                source_key=chunk.source_key,
                title=chunk.title,
                content=chunk.content,
                locale=chunk.locale,
                canonical_uri=chunk.canonical_uri,
                label=new_label,
                vector_score=chunk.vector_score,
                fts_rank=chunk.fts_rank,
                fused_score=chunk.fused_score,
                safety_tags=chunk.safety_tags,
            )
            merged.append(labeled_chunk)
            existing_keys.add(chunk.source_key)
    return merged


class ChatTurnOrchestrator:
    def __init__(
        self,
        *,
        completion: ChatCompletionPort,
        embedding: ChatEmbeddingPort,
        retrieval: ChatKnowledgeRetrievalPort,
        context_builder: Any,
        uow_factory: Callable[[], AsyncUnitOfWorkPort],
        model: str,
        daily_turn_budget: int,
        generation_lease_seconds: int,
        global_concurrency: int,
        cache_policy: PromptCachePolicy | None = None,
        max_output_tokens: int = CHAT_MAX_OUTPUT_TOKENS,
        semaphore: asyncio.Semaphore | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        next_meals: ChatNextMealCandidates | None = None,
        follow_ups: ChatFollowUpPort | None = None,
    ) -> None:
        self._completion = completion
        self._embedding = embedding
        self._retrieval = retrieval
        self._context_builder = context_builder
        self._uow_factory = uow_factory
        self._model = model
        self._daily_turn_budget = daily_turn_budget
        self._generation_lease_seconds = generation_lease_seconds
        self._cache_policy = cache_policy
        # A configured cap below the model default still starves reasoning
        # tokens and returns a few cut-off words. Never go under the floor.
        self._max_output_tokens = min(
            CHAT_OUTPUT_TOKEN_CEILING,
            max(int(max_output_tokens), CHAT_MAX_OUTPUT_TOKENS),
        )
        self._semaphore = semaphore or asyncio.Semaphore(max(1, global_concurrency))
        self._circuit = circuit_breaker
        self._next_meals = next_meals
        self._follow_ups = follow_ups

    async def prepare_turn(
        self,
        *,
        user_id: str,
        content: str,
        idempotency_key: str,
        locale: str | None,
        header_timezone: str | None,
        user_language: str | None,
        intent: str | None = None,
        function_name: str | None = None,
        function_args: dict[str, Any] | None = None,
        thread_id: str | None = None,
    ) -> PreparedChatTurn:
        started = time.perf_counter()
        resolved_locale = resolve_chat_locale(locale, user_language)
        trimmed = content.strip()
        if len(trimmed) > CHAT_MAX_USER_MESSAGE_CHARS:
            trimmed = trimmed[:CHAT_MAX_USER_MESSAGE_CHARS]
        fingerprint = request_fingerprint(
            trimmed,
            resolved_locale,
            intent,
            function_name=function_name,
            function_args=function_args,
        )
        await self._enforce_daily_budget(
            user_id, exclude_idempotency_key=idempotency_key
        )
        claim = await self._claim(
            user_id=user_id,
            content=trimmed,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            thread_id=thread_id,
        )
        slot_acquired = False
        if claim.kind != ChatClaimKind.REPLAY:
            if self._circuit_is_open():
                await self._fail(
                    claim.assistant_message.id,
                    "CHAT_PROVIDER_UNAVAILABLE",
                    generation_id=claim.assistant_message.generation_id,
                )
                raise ChatProviderUnavailableError(
                    retry_after_seconds=30, retryable=True
                )
            slot_acquired = await self._try_acquire_slot()
            if not slot_acquired:
                await self._fail(
                    claim.assistant_message.id,
                    "CHAT_PROVIDER_UNAVAILABLE",
                    generation_id=claim.assistant_message.generation_id,
                )
                raise ChatProviderUnavailableError(
                    retry_after_seconds=5, retryable=True
                )
        increment_metric(
            "chat.turn.claimed",
            attributes={"kind": claim.kind.value, "locale": resolved_locale},
        )
        return PreparedChatTurn(
            claim=claim,
            content=trimmed,
            locale=resolved_locale,
            header_timezone=header_timezone,
            started=started,
            intent=intent,
            function_name=function_name,
            function_args=function_args,
            slot_acquired=slot_acquired,
        )

    async def prepare_regeneration(
        self,
        *,
        user_id: str,
        assistant_message_id: str,
        content: str,
        locale: str | None,
        header_timezone: str | None,
        user_language: str | None,
        thread_id: str | None = None,
    ) -> PreparedChatTurn:
        """Replace the latest completed reply without adding another user turn.

        A different ``content`` edits the stored question before the new answer.
        """
        started = time.perf_counter()
        resolved_locale = resolve_chat_locale(locale, user_language)
        await self._enforce_daily_budget(user_id)
        lease = utc_now() + timedelta(seconds=self._generation_lease_seconds)
        async with self._uow_factory() as uow:
            repo = _chat_repo(uow)
            # A thread the user cannot see is "not found", not "not allowed".
            if thread_id is not None and (
                await repo.get_thread(user_id, thread_id) is None
            ):
                raise ChatThreadNotFoundError()
            reopened = await repo.reopen_assistant_message(
                user_id=user_id,
                assistant_message_id=assistant_message_id,
                lease_expires_at=lease,
                content=content.strip(),
                thread_id=thread_id,
            )
        if reopened is None:
            raise ChatRegenerateNotAllowedError()
        claim, prior_intent, content_changed = reopened
        if self._circuit_is_open():
            await self._fail(
                claim.assistant_message.id,
                "CHAT_PROVIDER_UNAVAILABLE",
                generation_id=claim.assistant_message.generation_id,
            )
            raise ChatProviderUnavailableError(retry_after_seconds=30, retryable=True)
        slot_acquired = await self._try_acquire_slot()
        if not slot_acquired:
            await self._fail(
                claim.assistant_message.id,
                "CHAT_PROVIDER_UNAVAILABLE",
                generation_id=claim.assistant_message.generation_id,
            )
            raise ChatProviderUnavailableError(retry_after_seconds=5, retryable=True)
        increment_metric(
            "chat.turn.regenerated",
            attributes={"locale": resolved_locale},
        )
        return PreparedChatTurn(
            claim=claim,
            content=(claim.user_message.content or "").strip(),
            locale=resolved_locale,
            header_timezone=header_timezone,
            started=started,
            # An edited question must not keep the previous chip intent.
            intent=None if content_changed else prior_intent,
            slot_acquired=slot_acquired,
        )

    async def stream_turn(
        self,
        *,
        user_id: str,
        content: str,
        idempotency_key: str,
        locale: str | None,
        header_timezone: str | None,
        user_language: str | None,
        intent: str | None = None,
    ) -> AsyncIterator[ChatSseEvent]:
        prepared = await self.prepare_turn(
            user_id=user_id,
            content=content,
            idempotency_key=idempotency_key,
            locale=locale,
            header_timezone=header_timezone,
            user_language=user_language,
            intent=intent,
        )
        async for event in self.stream_prepared(user_id=user_id, prepared=prepared):
            yield event

    async def stream_prepared(
        self,
        *,
        user_id: str,
        prepared: PreparedChatTurn,
    ) -> AsyncIterator[ChatSseEvent]:
        claim = prepared.claim
        trimmed = prepared.content
        resolved_locale = prepared.locale
        header_timezone = prepared.header_timezone
        started = prepared.started
        intent = prepared.intent
        sentences: list[str] = []
        suggestions: list[dict[str, Any]] = []
        discover_session_id: str | None = None
        follow_task: asyncio.Task[list[dict[str, str]]] | None = None
        generation: dict[str, Any] = {
            "text": "",
            "usage": ChatUsage(model=self._model),
            "provider_response_id": None,
            "blocked": False,
            "truncated": False,
            "tools": [],
        }

        try:
            if claim.kind == ChatClaimKind.REPLAY:
                async for event in self._replay(claim):
                    yield event
                distribution_metric(
                    "chat.turn.latency_ms",
                    (time.perf_counter() - started) * 1000,
                    unit="millisecond",
                    attributes={"kind": "replay"},
                )
                return

            yield _started_event(claim)

            if prepared.function_name:
                resolved = resolve_coach_function(
                    prepared.function_name, args=prepared.function_args
                )
            else:
                resolved = resolve_coach_function(intent)
            # Typed "What should I eat next?" must not depend on the model
            # remembering to call suggest_next_meal.
            if resolved is None and is_meal_suggestion_request(trimmed):
                typed_slot = slot_from_user_text(trimmed)
                resolved = resolve_coach_function(
                    FUNCTION_SUGGEST_NEXT_MEAL,
                    args={"slot": typed_slot} if typed_slot else {},
                )
                intent = ChatIntent.NEXT_MEAL.value
            direct_invoke = resolved is not None and not needs_retrieval(resolved.name)
            if direct_invoke:
                context = await self._context_builder.build(
                    user_id=user_id,
                    locale=resolved_locale,
                    header_timezone=header_timezone,
                )
                chunks: list[RetrievedKnowledgeChunk] = []
                query_embedding = None
            else:
                context, chunks, query_embedding = await self._ground(
                    user_id=user_id,
                    query=trimmed,
                    locale=resolved_locale,
                    header_timezone=header_timezone,
                )
            history_messages = await self._completed_history(
                claim.thread.id, exclude_message_id=claim.user_message.id
            )
            memory = rolling_memory(
                history_messages,
                stored_summary=claim.thread.summary,
                summary_through_message_id=claim.thread.summary_through_message_id,
            )
            if memory.through_message_id and memory.text:
                await self._save_thread_summary(
                    claim.thread.id,
                    memory.text,
                    memory.through_message_id,
                )
            earlier = memory.text
            history = _history_turns(history_messages)
            discover_session_id = last_discover_session_id(history_messages)
            meal_slot = resolve_meal_slot(context.suggested_meal_slot, trimmed)
            # Ingredient and recipe questions stay on the card already shown,
            # even when the dish name contains a word like "vegetarian".
            answer_from_shown_meals = (
                resolved is None
                and bool(_latest_shown_meal_names(history_messages))
                and (
                    is_meal_comparison(trimmed)
                    or is_meal_explanation(trimmed)
                    or is_cook_request(trimmed)
                    or is_ingredient_request(trimmed)
                )
            )
            # "Make the tofu higher protein" should refresh the cards, not only
            # the prose. How/why questions stay on the free-text path.
            if (
                resolved is None
                and is_meal_refinement(trimmed)
                and not answer_from_shown_meals
            ):
                typed_slot = slot_from_user_text(trimmed)
                revise = _meal_revision_text(
                    trimmed, _latest_shown_meal_names(history_messages)
                )
                args: dict[str, Any] = {}
                if typed_slot:
                    args["slot"] = typed_slot
                if revise != trimmed:
                    args["query"] = revise
                resolved = resolve_coach_function(
                    FUNCTION_SUGGEST_NEXT_MEAL,
                    args=args,
                )
                intent = ChatIntent.NEXT_MEAL.value
                direct_invoke = resolved is not None and not needs_retrieval(
                    resolved.name
                )

            # Chip or direct suggest_next_meal: prefetch recipe cards; skip knowledge retrieve.
            should_prefetch_next_meal = intent == ChatIntent.NEXT_MEAL.value or (
                resolved is not None and resolved.name == FUNCTION_SUGGEST_NEXT_MEAL
            )
            meal_user_text = trimmed
            if resolved is not None:
                query = resolved.args.get("query")
                if isinstance(query, str) and query.strip():
                    meal_user_text = query.strip()
            if should_prefetch_next_meal and self._next_meals is not None:
                ack = next_meal_ack(locale=resolved_locale, slot=meal_slot)
                sentences.append(ack)
                yield ChatSseEvent(
                    event="message.delta",
                    data={
                        "assistant_message_id": claim.assistant_message.id,
                        "delta": ack,
                    },
                )
                try:
                    batch = await self._next_meals.fetch(
                        user_id=user_id,
                        context=context,
                        user_text=meal_user_text,
                        locale=resolved_locale,
                        session_id=discover_session_id,
                        slot=meal_slot,
                        include_images=False,
                    )
                    suggestions = batch.suggestions
                    discover_session_id = batch.session_id or discover_session_id
                    meal_slot = batch.meal_slot
                    if suggestions:
                        # Show the cards while the sentence is still being written.
                        yield ChatSseEvent(
                            event="message.suggestions",
                            data={
                                "assistant_message_id": claim.assistant_message.id,
                                "suggestions": suggestions,
                            },
                        )
                except Exception:
                    logger.warning(
                        "Upfront next meal candidate generation failed",
                        extra={"user_id": user_id, "meal_slot": meal_slot},
                        exc_info=True,
                    )

            tools = (
                [] if direct_invoke or answer_from_shown_meals else list(openai_tools())
            )

            generation = {
                "text": "",
                "usage": ChatUsage(model=self._model),
                "provider_response_id": None,
                "blocked": False,
                "tools": tools,
            }

            # Direct invoke: run the known function once, then one tool-free synthesis.
            current_user_message = trimmed
            if direct_invoke and resolved is not None:
                call_id = f"direct_{uuid.uuid4().hex[:8]}"
                (
                    result,
                    intent,
                    suggestions,
                    discover_session_id,
                    meal_slot,
                    chunks,
                ) = await self._execute_coach_function(
                    name=resolved.name,
                    args=resolved.args,
                    user_id=user_id,
                    trimmed=trimmed,
                    resolved_locale=resolved_locale,
                    context=context,
                    chunks=chunks,
                    suggestions=suggestions,
                    discover_session_id=discover_session_id,
                    meal_slot=meal_slot,
                )
                history.append(
                    ChatHistoryTurn(
                        role=ChatMessageRole.USER,
                        content=current_user_message,
                    )
                )
                history.append(
                    ChatHistoryTurn(
                        role=ChatMessageRole.ASSISTANT,
                        content="",
                        tool_calls=[
                            {
                                "id": call_id,
                                "name": resolved.name,
                                "args": resolved.args,
                            }
                        ],
                    )
                )
                history.append(
                    ChatHistoryTurn(
                        role="tool",
                        tool_call_id=call_id,
                        name=resolved.name,
                        content=result,
                    )
                )
                current_user_message = ""
                generation["tools"] = None

            meal_reply = ""
            if intent == ChatIntent.NEXT_MEAL.value and suggestions:
                # Cards are already chosen. Stream the model's sentence, and
                # replace it with the template when that sentence is not usable.
                streamed: list[str] = []
                async for delta_text, replace in self._iter_meal_reply_deltas(
                    context=context,
                    chunks=chunks,
                    history=history,
                    user_message=current_user_message,
                    generation=generation,
                    intent=intent,
                    suggestions=suggestions,
                    history_messages=history_messages,
                    earlier=earlier,
                ):
                    streamed.append(delta_text)
                    yield ChatSseEvent(
                        event="message.delta",
                        data={
                            "assistant_message_id": claim.assistant_message.id,
                            "delta": delta_text,
                            "replace": replace,
                        },
                    )
                meal_reply = (
                    "" if generation.get("blocked") else "".join(streamed).strip()
                )
                eaten = eaten_today_name(
                    context.recent_meals,
                    local_date=context.local_date,
                    suggestion_names=[
                        str(card.get("name") or "")
                        for card in suggestions
                        if isinstance(card, dict)
                    ],
                )
                if not meal_reply_is_usable(meal_reply, suggestions, context=context):
                    meal_reply = (
                        next_meal_intro(
                            suggestions,
                            locale=resolved_locale,
                            slot=meal_slot,
                            user_text=trimmed,
                            eaten_today=eaten,
                            remaining_protein_g=context.remaining_protein_g,
                            remaining_carbs_g=context.remaining_carbs_g,
                            remaining_fat_g=context.remaining_fat_g,
                        )
                        or ""
                    )
                else:
                    meal_reply = with_eaten_today(
                        meal_reply, eaten, locale=resolved_locale
                    )
                if meal_reply and meal_reply != "".join(streamed).strip():
                    yield ChatSseEvent(
                        event="message.delta",
                        data={
                            "assistant_message_id": claim.assistant_message.id,
                            "delta": meal_reply,
                            "replace": True,
                        },
                    )
            shown_cards = (
                _shown_meal_cards(history_messages) if answer_from_shown_meals else []
            )
            card_reply = None
            if shown_cards:
                card_reply = verified_ingredient_reply(
                    trimmed,
                    shown_cards,
                    locale=resolved_locale,
                ) or verified_cook_reply(
                    trimmed,
                    shown_cards,
                    locale=resolved_locale,
                )
            if meal_reply:
                # The acknowledgement was only a placeholder while cards loaded.
                sentences.clear()
                sentences.append(meal_reply)
                generation["text"] = meal_reply
                generation["blocked"] = False
                generation["truncated"] = False
            elif should_prefetch_next_meal and not suggestions:
                # The picking line is already on screen. A model sentence here
                # would invent a dish, because there is no card to name.
                unavailable = next_meal_unavailable(
                    locale=resolved_locale, slot=meal_slot
                )
                streamed_text = "".join(sentences).strip()
                sentences.clear()
                sentences.append(unavailable)
                generation["text"] = unavailable
                generation["blocked"] = False
                generation["truncated"] = False
                generation["tools"] = None
                if unavailable != streamed_text:
                    yield ChatSseEvent(
                        event="message.delta",
                        data={
                            "assistant_message_id": claim.assistant_message.id,
                            "delta": unavailable,
                            "replace": True,
                        },
                    )
            elif card_reply:
                # The card already has the steps or ingredients. Do not stream
                # an invented sentence and then replace it. Keep that card on
                # this reply so Log and See recipe stay next to the answer.
                streamed_text = "".join(sentences).strip()
                sentences.clear()
                sentences.append(card_reply)
                generation["text"] = card_reply
                generation["blocked"] = False
                generation["truncated"] = False
                generation["tools"] = None
                suggestions = _cards_named_in_reply(card_reply, shown_cards)
                yield ChatSseEvent(
                    event="message.delta",
                    data={
                        "assistant_message_id": claim.assistant_message.id,
                        "delta": card_reply,
                        "replace": bool(streamed_text),
                    },
                )
                if suggestions:
                    yield ChatSseEvent(
                        event="message.suggestions",
                        data={
                            "assistant_message_id": claim.assistant_message.id,
                            "suggestions": suggestions,
                        },
                    )
            else:
                # Keep streaming until no tool calls are emitted (max 2 iterations)
                max_tool_iterations = 1 if direct_invoke else 2
                iteration = 0
                while iteration < max_tool_iterations:
                    iteration += 1
                    async for delta_text in self._iter_validated_sentences(
                        context=context,
                        chunks=chunks,
                        history=history,
                        user_message=current_user_message,
                        generation=generation,
                        intent=intent,
                        meal_candidates=suggestions,
                        traceable_meals=_traceable_meals(suggestions, history_messages),
                        earlier=earlier,
                    ):
                        if not delta_text:
                            continue
                        sentences.append(delta_text)
                        yield ChatSseEvent(
                            event="message.delta",
                            data={
                                "assistant_message_id": claim.assistant_message.id,
                                "delta": delta_text,
                            },
                        )

                    tool_calls = generation.get("tool_calls")
                    # A how/why question about the cards stays a written answer.
                    # Drop a tool call that would replace those cards.
                    if answer_from_shown_meals and tool_calls:
                        generation["tool_calls"] = None
                        tool_calls = None
                    if not tool_calls:
                        break
                    if current_user_message:
                        history.append(
                            ChatHistoryTurn(
                                role=ChatMessageRole.USER,
                                content=current_user_message,
                            )
                        )
                        current_user_message = ""
                    history.append(
                        ChatHistoryTurn(
                            role=ChatMessageRole.ASSISTANT,
                            content=generation.get("text") or "",
                            tool_calls=tool_calls,
                        )
                    )
                    for call in tool_calls:
                        name = call.get("name")
                        call_id = call.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                        raw_args = call.get("args") or {}
                        if isinstance(raw_args, str):
                            try:
                                args = json.loads(raw_args)
                            except Exception:
                                args = {}
                        elif isinstance(raw_args, dict):
                            args = raw_args
                        else:
                            args = {}
                        (
                            result,
                            intent,
                            suggestions,
                            discover_session_id,
                            meal_slot,
                            chunks,
                        ) = await self._execute_coach_function(
                            name=name,
                            args=args,
                            user_id=user_id,
                            trimmed=trimmed,
                            resolved_locale=resolved_locale,
                            context=context,
                            chunks=chunks,
                            suggestions=suggestions,
                            discover_session_id=discover_session_id,
                            meal_slot=meal_slot,
                        )
                        history.append(
                            ChatHistoryTurn(
                                role="tool",
                                tool_call_id=call_id,
                                name=name,
                                content=result,
                            )
                        )
                    generation["tool_calls"] = None
                    generation["tools"] = None

                if _should_retry_output_limit(generation, sentences):
                    generation["output_limit_retried"] = True
                    # The retry finishes the cut-off sentence. Leaving tools
                    # on lets the model call one and yield no text, which the
                    # caller then replaces with the safety fallback.
                    generation["tools"] = None
                    generation["tool_calls"] = None
                    already_streamed = bool("".join(sentences).strip())
                    first_pass = list(sentences)
                    first_usage = generation.get("usage") or ChatUsage(
                        model=self._model
                    )
                    sentences.clear()
                    increment_metric(
                        "chat.turn.output_truncated",
                        attributes={"locale": resolved_locale},
                    )
                    first_retry_delta = True
                    try:
                        async for delta_text in self._iter_validated_sentences(
                            context=context,
                            chunks=chunks,
                            history=history,
                            user_message=current_user_message,
                            generation=generation,
                            intent=intent,
                            meal_candidates=suggestions,
                            traceable_meals=_traceable_meals(
                                suggestions, history_messages
                            ),
                            earlier=earlier,
                            output_token_budget=min(
                                self._max_output_tokens * 2,
                                CHAT_OUTPUT_TOKEN_CEILING,
                            ),
                        ):
                            if not delta_text:
                                continue
                            sentences.append(delta_text)
                            yield ChatSseEvent(
                                event="message.delta",
                                data={
                                    "assistant_message_id": claim.assistant_message.id,
                                    "delta": delta_text,
                                    "replace": already_streamed and first_retry_delta,
                                },
                            )
                            first_retry_delta = False
                    except BaseException:
                        if sentences:
                            # The client already replaced the stump with this
                            # retry text. Save that partial, not the first pass.
                            retry_usage = generation.get("usage")
                            generation["usage"] = _combine_chat_usage(
                                first_usage,
                                retry_usage if retry_usage is not first_usage else None,
                            )
                        else:
                            # Nothing from the retry reached the client.
                            sentences[:] = first_pass
                            generation["usage"] = first_usage
                        raise
                    retry_usage = generation.get("usage")
                    generation["usage"] = _combine_chat_usage(
                        first_usage,
                        retry_usage if retry_usage is not first_usage else None,
                    )

            streamed_text = "".join(sentences).strip()
            final_text = streamed_text
            usage = generation["usage"]
            provider_response_id = generation["provider_response_id"]
            blocked = bool(generation["blocked"])
            if blocked or generation.get("truncated"):
                # A safety stop or a token cap leaves a stump ("Nếu",
                # "Không sao, chênh lệch"). Keep finished sentences only.
                final_text = finished_assistant_text(final_text)
                generation["text"] = final_text
            if answer_from_shown_meals and card_reply:
                if not cook_reply_already_said(final_text, card_reply):
                    final_text = card_reply
                    sentences.clear()
                    sentences.append(card_reply)
                    generation["text"] = card_reply
                    generation["blocked"] = False
                    generation["truncated"] = False
                    blocked = False
            if not final_text:
                blocked = True
                final_text = safe_fallback_message(resolved_locale)
                generation["text"] = final_text
            if final_text != streamed_text:
                # A truncated retry that produced nothing must still replace the
                # stump the client already showed.
                yield ChatSseEvent(
                    event="message.delta",
                    data={
                        "assistant_message_id": claim.assistant_message.id,
                        "delta": final_text,
                        "replace": bool(streamed_text)
                        or bool(generation.get("output_limit_retried")),
                    },
                )

            citations = _citations_for(final_text, chunks)
            ready_payload = _reply_payload(
                suggestions=suggestions,
                follow_ups=[],
                discover_session_id=discover_session_id,
                intent=intent,
                citations=citations,
                nutrition_snapshot=(
                    context.to_nutrition_snapshot()
                    if context.has_complete_nutrition
                    else None
                ),
            )
            ready_payload.pop("follow_ups", None)
            # Chips only need the finished sentence. Do not wait for meal photos.
            follow_task = asyncio.create_task(
                self._generate_follow_ups(
                    locale=resolved_locale,
                    intent=intent,
                    slot=meal_slot,
                    user_message=trimmed,
                    assistant_text=final_text,
                    has_suggestions=bool(suggestions),
                    suggestion_names=meal_names(suggestions),
                )
            )
            completed = await self._complete(
                claim.assistant_message,
                content=final_text,
                usage=usage,
                citations=citations,
                provider_response_id=provider_response_id,
                reply_payload=_reply_payload(
                    suggestions=suggestions,
                    follow_ups=[],
                    discover_session_id=discover_session_id,
                    intent=intent,
                    citations=citations,
                    nutrition_snapshot=(
                        context.to_nutrition_snapshot()
                        if context.has_complete_nutrition
                        else None
                    ),
                ),
            )
            if completed is None:
                follow_task.cancel()
                increment_metric(
                    "chat.turn.stale_generation",
                    attributes={"locale": resolved_locale},
                )
                yield ChatSseEvent(
                    event="message.error",
                    data={
                        "code": "CHAT_TURN_FAILED",
                        "retryable": True,
                        "assistant_message_id": claim.assistant_message.id,
                    },
                )
                return
            yield ChatSseEvent(
                event="message.ready",
                data={
                    "assistant_message_id": completed.id,
                    "citations": [
                        {
                            "label": item.label,
                            "source_key": item.source_key,
                            "title": item.title,
                            "canonical_uri": item.canonical_uri,
                        }
                        for item in citations
                    ],
                    **ready_payload,
                },
            )
            follow_ups = await follow_task
            if follow_ups:
                try:
                    await self._attach_follow_ups(completed.id, follow_ups)
                except Exception:
                    logger.info(
                        "chat follow-up save failed",
                        extra={"assistant_message_id": completed.id},
                    )
            if blocked:
                increment_metric(
                    "chat.turn.safety_block", attributes={"locale": resolved_locale}
                )
            increment_metric(
                "chat.turn.completed",
                attributes={"model": self._model, "no_evidence": str(not chunks)},
            )
            if usage.input_tokens or usage.output_tokens:
                increment_metric(
                    "chat.tokens.input",
                    value=float(usage.input_tokens),
                    attributes={"model": self._model},
                )
                increment_metric(
                    "chat.tokens.output",
                    value=float(usage.output_tokens),
                    attributes={"model": self._model},
                )
            yield ChatSseEvent(
                event="message.completed",
                data={
                    "assistant_message_id": completed.id,
                    "thread_id": claim.thread.id,
                    "model": self._model,
                    "prompt_version": CHAT_PROMPT_VERSION,
                    "context_version": CHAT_CONTEXT_VERSION,
                    "usage": {
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "cached_tokens": usage.cached_tokens,
                    },
                    "citations": [
                        {
                            "label": item.label,
                            "source_key": item.source_key,
                            "title": item.title,
                            "canonical_uri": item.canonical_uri,
                        }
                        for item in citations
                    ],
                    **_completed_sidecar(
                        completed,
                        follow_ups,
                        suggestions=suggestions,
                    ),
                },
            )
            # Photo generation can take tens of seconds. The reply is already
            # done, so free the chat slot before that wait.
            self.release_slot(prepared)
            if suggestions:
                imaged = await self._attach_meal_images(suggestions)
                if imaged is not suggestions:
                    suggestions = imaged
                    await self._store_meal_suggestions(completed.id, suggestions)
                    yield ChatSseEvent(
                        event="message.suggestions",
                        data={
                            "assistant_message_id": completed.id,
                            "suggestions": suggestions,
                        },
                    )
        except asyncio.CancelledError:
            partial = sanitize_incomplete_assistant_text("".join(sentences).strip())
            if partial:
                try:
                    await self._complete(
                        claim.assistant_message,
                        content=partial,
                        usage=generation["usage"],
                        citations=[],
                        provider_response_id=generation.get("provider_response_id"),
                        reply_payload=_reply_payload(
                            suggestions=suggestions,
                            follow_ups=[],
                            discover_session_id=discover_session_id,
                            intent=intent,
                        ),
                    )
                except Exception:
                    await self._fail(
                        claim.assistant_message.id,
                        "CHAT_CANCELLED",
                        generation_id=claim.assistant_message.generation_id,
                        content=partial,
                    )
            else:
                await self._fail(
                    claim.assistant_message.id,
                    "CHAT_CANCELLED",
                    generation_id=claim.assistant_message.generation_id,
                )
            increment_metric("chat.turn.cancelled")
            raise
        except ChatProviderUnavailableError as exc:
            await self._save_partial_or_fail(
                claim,
                sentences=sentences,
                generation=generation,
                suggestions=suggestions,
                discover_session_id=discover_session_id,
                intent=intent,
                error_code="CHAT_PROVIDER_UNAVAILABLE",
            )
            increment_metric(
                "chat.turn.failed", attributes={"code": "CHAT_PROVIDER_UNAVAILABLE"}
            )
            yield ChatSseEvent(
                event="message.error",
                data={
                    "code": "CHAT_PROVIDER_UNAVAILABLE",
                    "retryable": exc.retryable,
                    "retry_after": exc.retry_after_seconds,
                    "assistant_message_id": claim.assistant_message.id,
                },
            )
        except Exception:
            logger.warning(
                "chat turn failed",
                extra={
                    "thread_id": claim.thread.id,
                    "assistant_message_id": claim.assistant_message.id,
                    "error_code": "CHAT_TURN_FAILED",
                },
                exc_info=True,
            )
            await self._save_partial_or_fail(
                claim,
                sentences=sentences,
                generation=generation,
                suggestions=suggestions,
                discover_session_id=discover_session_id,
                intent=intent,
                error_code="CHAT_TURN_FAILED",
            )
            yield ChatSseEvent(
                event="message.error",
                data={
                    "code": "CHAT_TURN_FAILED",
                    "retryable": True,
                    "assistant_message_id": claim.assistant_message.id,
                },
            )
            increment_metric(
                "chat.turn.failed", attributes={"code": "CHAT_TURN_FAILED"}
            )
        finally:
            if follow_task is not None and not follow_task.done():
                follow_task.cancel()
                try:
                    await follow_task
                except asyncio.CancelledError:
                    pass
            self.release_slot(prepared)
            distribution_metric(
                "chat.turn.latency_ms",
                (time.perf_counter() - started) * 1000,
                unit="millisecond",
                attributes={"kind": "generate"},
            )

    def release_slot(self, prepared: PreparedChatTurn) -> None:
        if not prepared.slot_acquired:
            return
        prepared.slot_acquired = False
        self._semaphore.release()

    async def get_thread(
        self,
        *,
        user_id: str,
        limit: int,
        before: str | None,
        locale: str = "en",
        header_timezone: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Return one page of a thread.

        Without *thread_id* this is the most recently active thread, which is
        what single-thread clients have always read.
        """
        async with self._uow_factory() as uow:
            repo = _chat_repo(uow)
            found: ChatThread | None
            if thread_id is None:
                found = await repo.get_or_create_thread(user_id)
            else:
                found = await repo.get_thread(user_id, thread_id)
            if found is None:
                raise ChatThreadNotFoundError()
            thread = found
            messages = await repo.list_completed_messages(
                thread_id=thread.id,
                limit=limit + 1,
                before_message_id=before,
            )
            generating = await repo.get_generating_turn(thread.id)
            source_keys = [
                key for message in messages for key in message.citation_source_keys
            ]
            citation_metadata = await repo.list_citation_metadata(source_keys)
        has_more = len(messages) > limit
        page = messages[:limit]
        # Newest-first page → oldest-first; tie-break user before assistant when
        # both rows share the same claim-time created_at.
        chronological = chronological_chat_messages(page)
        welcome = None
        if before is None and not chronological and generating is None:
            welcome = await self._welcome_snapshot(
                user_id=user_id,
                locale=locale,
                header_timezone=header_timezone,
            )
        return {
            "thread": _thread_payload(thread),
            "messages": [
                _public_message(
                    message,
                    _hydrate_message_citations(message, citation_metadata),
                )
                for message in chronological
            ],
            "has_more": has_more,
            "in_flight": _in_flight_payload(generating),
            "welcome": welcome,
        }

    async def _welcome_snapshot(
        self,
        *,
        user_id: str,
        locale: str,
        header_timezone: str | None,
    ) -> dict[str, Any] | None:
        try:
            context = await self._context_builder.build(
                user_id=user_id,
                locale=locale,
                header_timezone=header_timezone,
            )
        except Exception:
            logger.warning(
                "chat welcome snapshot failed",
                extra={"user_id": user_id},
                exc_info=True,
            )
            return None
        return coach_welcome(context)

    async def list_threads(
        self,
        *,
        user_id: str,
        limit: int,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Raises ChatInvalidCursorError for a cursor this server did not issue."""
        before = None
        if cursor is not None:
            before = decode_thread_cursor(cursor)
            if before is None:
                raise ChatInvalidCursorError()
        async with self._uow_factory() as uow:
            listings = await _chat_repo(uow).list_threads(
                user_id=user_id,
                limit=limit + 1,
                before=before,
            )
        page = listings[:limit]
        has_more = len(listings) > limit
        next_cursor = None
        if has_more and page:
            last = page[-1].thread
            next_cursor = encode_thread_cursor(last.updated_at, last.id)
        return {
            "threads": [_thread_listing_payload(item) for item in page],
            "has_more": has_more,
            "next_cursor": next_cursor,
        }

    async def create_thread(self, user_id: str) -> dict[str, Any]:
        async with self._uow_factory() as uow:
            thread = await _chat_repo(uow).create_thread(user_id)
        increment_metric("chat.thread.created")
        return _thread_payload(thread)

    async def clear_thread(
        self, user_id: str, thread_id: str | None = None
    ) -> dict[str, Any]:
        async with self._uow_factory() as uow:
            repo = _chat_repo(uow)
            thread = await repo.clear_thread(user_id, thread_id)
        increment_metric("chat.thread.cleared")
        return {"thread_id": thread.id, "cleared": True}

    async def _enforce_daily_budget(
        self,
        user_id: str,
        *,
        exclude_idempotency_key: str | None = None,
    ) -> None:
        start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
        async with self._uow_factory() as uow:
            repo = _chat_repo(uow)
            used = await repo.count_user_turns_since(
                user_id=user_id,
                since=start,
                exclude_idempotency_key=exclude_idempotency_key,
            )
        if used >= self._daily_turn_budget:
            raise ChatRateLimitedError(retry_after_seconds=3600, daily=True)

    async def _claim(
        self,
        *,
        user_id: str,
        content: str,
        idempotency_key: str,
        request_fingerprint: str,
        thread_id: str | None = None,
    ) -> ChatTurnClaim:
        lease = utc_now() + timedelta(seconds=self._generation_lease_seconds)
        async with self._uow_factory() as uow:
            repo = _chat_repo(uow)
            return await repo.claim_turn(
                user_id=user_id,
                content=content,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                lease_expires_at=lease,
                thread_id=thread_id,
            )

    async def _replay(self, claim: ChatTurnClaim) -> AsyncIterator[ChatSseEvent]:
        citations = await self._citations_for_message(claim.assistant_message)
        yield _started_event(claim)
        content = claim.assistant_message.content or ""
        if content:
            yield ChatSseEvent(
                event="message.delta",
                data={
                    "assistant_message_id": claim.assistant_message.id,
                    "delta": content,
                },
            )
        yield ChatSseEvent(
            event="message.completed",
            data={
                "assistant_message_id": claim.assistant_message.id,
                "thread_id": claim.thread.id,
                "model": claim.assistant_message.model or self._model,
                "prompt_version": claim.assistant_message.prompt_version,
                "context_version": claim.assistant_message.context_version,
                "replayed": True,
                "usage": {
                    "input_tokens": claim.assistant_message.input_tokens or 0,
                    "output_tokens": claim.assistant_message.output_tokens or 0,
                    "cached_tokens": claim.assistant_message.cached_tokens or 0,
                },
                "citations": citations,
                **reply_sidecar(claim.assistant_message),
            },
        )

    async def _execute_coach_function(
        self,
        *,
        name: str | None,
        args: dict[str, Any],
        user_id: str,
        trimmed: str,
        resolved_locale: str,
        context: ChatUserContext,
        chunks: list[RetrievedKnowledgeChunk],
        suggestions: list[dict[str, Any]],
        discover_session_id: str | None,
        meal_slot: str,
    ) -> tuple[
        str,
        str | None,
        list[dict[str, Any]],
        str | None,
        str,
        list[RetrievedKnowledgeChunk],
    ]:
        """Run a registry function. Shared by direct invoke and model tool calls."""
        intent: str | None = preferred_alias(name or "", args)
        result: str

        if name == FUNCTION_SUGGEST_NEXT_MEAL:
            intent = ChatIntent.NEXT_MEAL.value
            slot_arg = args.get("slot")
            query_arg = args.get("query")
            target_slot = slot_arg or meal_slot
            fetch_text = query_arg or trimmed
            if not suggestions and self._next_meals is not None:
                try:
                    batch = await self._next_meals.fetch(
                        user_id=user_id,
                        context=context,
                        user_text=fetch_text,
                        locale=resolved_locale,
                        session_id=discover_session_id,
                        slot=target_slot,
                        include_images=False,
                    )
                    suggestions = batch.suggestions
                    discover_session_id = batch.session_id or discover_session_id
                    meal_slot = batch.meal_slot
                except Exception as exc:
                    logger.warning("Failed to fetch next meal candidates: %s", exc)
                    result = "Meal catalog service is temporarily unavailable."
                    return (
                        result,
                        intent,
                        suggestions,
                        discover_session_id,
                        meal_slot,
                        chunks,
                    )
            if suggestions:
                primary = suggestions[0]
                primary_name = primary.get("name", "")
                primary_cal = primary.get("calories", "")
                primary_p = primary.get("protein_g", "")
                primary_c = primary.get("carbs_g", "")
                primary_f = primary.get("fat_g", "")
                prep_m = primary.get("prep_time_minutes")
                ingredients_summary = ""
                raw_ings = primary.get("ingredients")
                if raw_ings and isinstance(raw_ings, list):
                    ingredients_summary = ", ".join(
                        f"{ing.get('name')} ({ing.get('amount')}{ing.get('unit')})"
                        if isinstance(ing, dict)
                        else str(ing)
                        for ing in raw_ings[:5]
                    )
                option_word = "option" if len(suggestions) == 1 else "options"
                lines = [
                    f"Found {len(suggestions)} meal {option_word} for {meal_slot}: {primary_name}.",
                    f"Portion: {primary_cal} kcal ({primary_p}g protein, {primary_c}g carbs, {primary_f}g fat).",
                ]
                other_names = [
                    str(card.get("name")).strip()
                    for card in suggestions[1:]
                    if isinstance(card, dict) and str(card.get("name") or "").strip()
                ]
                if other_names:
                    lines.append(
                        "Other options already on the card: "
                        + ", ".join(other_names)
                        + "."
                    )
                primary_fiber = primary.get("fiber_g")
                if primary_fiber:
                    lines.append(f"Fiber: {primary_fiber}g.")
                score = primary.get("nrf_quality")
                if score is not None:
                    lines.append(f"Nutrient quality score: {score}/100.")
                micros = primary.get("micros")
                if isinstance(micros, dict) and micros:
                    notable = [
                        f"{k.replace('_', ' ')}: {v}"
                        for k, v in micros.items()
                        if v is not None and v > 0
                    ][:4]
                    if notable:
                        lines.append(f"Notable micronutrients: {', '.join(notable)}.")
                if prep_m:
                    lines.append(f"Prep time: {prep_m} minutes.")
                if ingredients_summary:
                    lines.append(f"Key ingredients: {ingredients_summary}.")
                lines.append(
                    "Note: The user will see this meal recipe card directly in the chat UI where they can tap to view cooking instructions and log it."
                )
                result = "\n".join(lines)
            elif self._next_meals is None:
                result = "Meal candidate service not configured."
            else:
                result = (
                    f"No meal options found for {meal_slot} matching current criteria."
                )

        elif name == FUNCTION_CHECK_DAILY_PROGRESS:
            focus = args.get("focus")
            if focus == "day_progress" or (
                not focus
                and (
                    "tiến độ" in trimmed.casefold() or "progress" in trimmed.casefold()
                )
            ):
                intent = ChatIntent.DAY_PROGRESS.value
            else:
                intent = ChatIntent.REMAINING_BUDGET.value
            if not context.has_complete_nutrition:
                result = _daily_progress_unavailable(resolved_locale)
            else:
                result = (
                    f"Daily progress: food consumed {context.food_calories} kcal; "
                    f"activity burned {context.movement_kcal_burned} kcal. "
                    f"Macros consumed: {context.consumed_protein_g}g P, {context.consumed_carbs_g}g C, {context.consumed_fat_g}g F. "
                    f"Remaining: {context.remaining_calories} kcal "
                    f"({context.remaining_protein_g}g P, {context.remaining_carbs_g}g C, {context.remaining_fat_g}g F). "
                    f"Daily targets: {context.target_calories} kcal ({context.target_protein_g}g P, {context.target_carbs_g}g C, {context.target_fat_g}g F). "
                    f"Remaining days: {context.remaining_days}."
                )

        elif name == FUNCTION_SEARCH_KNOWLEDGE:
            query_arg = args.get("query") or trimmed
            try:
                retrieved_chunks, _ = await self._retrieve(query_arg, resolved_locale)
                filtered = filter_chunks_for_allergies(
                    retrieved_chunks, context.allergies or []
                )
                chunks = _merge_retrieved_chunks(chunks, filtered)
                if filtered:
                    result = "Retrieved Nutree knowledge:\n" + "\n".join(
                        f"{c.label}: {c.title} - {c.content}" for c in filtered
                    )
                else:
                    result = "No verified Nutree knowledge found for this query."
            except Exception as exc:
                logger.warning("Failed to search nutrition knowledge: %s", exc)
                result = "Nutrition knowledge retrieval is temporarily unavailable."

        elif name == FUNCTION_EXPLAIN_LIMITS:
            intent = ChatIntent.LIMITS.value
            result = (
                "Nutree Coach capabilities and limits:\n"
                "- Can help with: explaining your log, suggesting meals that fit remaining "
                "budget, tracking daily/weekly nutritional progress, and providing "
                "evidence-based dietary guidance.\n"
                "- Cannot help with: logging meals for you, changing calorie or macro "
                "targets, medical diagnoses, prescriptions or medication advice, "
                "replacing licensed physicians or dietitians, or diagnosing eating disorders."
            )

        else:
            result = f"Tool '{name}' is not recognized."

        return result, intent, suggestions, discover_session_id, meal_slot, chunks

    async def _citations_for_message(
        self, message: ChatMessage
    ) -> list[dict[str, str | None]]:
        refs = message.citation_refs()
        keys = [key for _, key in refs] if refs else list(message.citation_source_keys)
        labels = [label for label, _ in refs] if refs else None
        if not keys:
            return []
        async with self._uow_factory() as uow:
            metadata = await _chat_repo(uow).list_citation_metadata(keys)
        return hydrate_citations(keys, metadata, labels=labels)

    async def _ground(
        self,
        *,
        user_id: str,
        query: str,
        locale: str,
        header_timezone: str | None,
    ) -> tuple[ChatUserContext, list[RetrievedKnowledgeChunk], list[float] | None]:
        context_task = asyncio.create_task(
            self._context_builder.build(
                user_id=user_id,
                locale=locale,
                header_timezone=header_timezone,
            )
        )
        retrieval_task = asyncio.create_task(self._retrieve(query, locale))
        context, retrieved = await asyncio.gather(context_task, retrieval_task)
        chunks, embedding = retrieved
        return (
            context,
            filter_chunks_for_allergies(chunks, context.allergies or []),
            embedding,
        )

    async def _retrieve(
        self, query: str, locale: str
    ) -> tuple[list[RetrievedKnowledgeChunk], list[float] | None]:
        embedding: list[float] | None = None
        try:
            embedding = await self._embedding.embed_query(query)
        except Exception:
            log_event(
                "warning",
                "chat embedding failed; continuing with full-text only",
                attributes={"error_code": "CHAT_EMBEDDING_FAILED"},
            )
        try:
            chunks = await self._retrieval.retrieve(
                query=query,
                query_embedding=embedding,
                locale=locale,
                allergies=[],
                limit=CHAT_RETRIEVAL_MAX_CHUNKS,
            )
        except Exception:
            log_event(
                "warning",
                "chat retrieval failed",
                attributes={"error_code": "CHAT_RETRIEVAL_FAILED"},
            )
            return [], embedding
        increment_metric(
            "chat.retrieval.hit" if chunks else "chat.retrieval.no_evidence"
        )
        return chunks, embedding

    async def _completed_history(
        self, thread_id: str, *, exclude_message_id: str
    ) -> list[ChatMessage]:
        async with self._uow_factory() as uow:
            repo = _chat_repo(uow)
            messages = await repo.list_recent_completed_history(
                thread_id=thread_id,
                limit=CHAT_MEMORY_FETCH + 1,
            )
        return [message for message in messages if message.id != exclude_message_id]

    async def _iter_meal_reply_deltas(
        self,
        *,
        context: ChatUserContext,
        chunks: list[RetrievedKnowledgeChunk],
        history: list[ChatHistoryTurn],
        user_message: str,
        generation: dict[str, Any],
        intent: str | None,
        suggestions: list[dict[str, Any]],
        history_messages: list[ChatMessage],
        earlier: str | None,
    ) -> AsyncIterator[tuple[str, bool]]:
        """Stream a next-meal sentence. The first chunk replaces the acknowledgement."""
        generation["tools"] = None
        generation["tool_calls"] = None
        first = True
        try:
            async for delta in self._iter_validated_sentences(
                context=context,
                chunks=chunks,
                history=history,
                user_message=user_message,
                generation=generation,
                intent=intent,
                meal_candidates=suggestions,
                traceable_meals=_traceable_meals(suggestions, history_messages),
                earlier=earlier,
            ):
                if not delta:
                    continue
                yield delta, first
                first = False
        except ChatProviderUnavailableError:
            return

    async def _iter_validated_sentences(
        self,
        *,
        context: ChatUserContext,
        chunks: list[RetrievedKnowledgeChunk],
        history: list[ChatHistoryTurn],
        user_message: str,
        generation: dict[str, Any],
        intent: str | None = None,
        meal_candidates: list[dict[str, Any]] | None = None,
        traceable_meals: list[dict[str, Any]] | None = None,
        earlier: str | None = None,
        output_token_budget: int | None = None,
    ) -> AsyncIterator[str]:
        if self._circuit_is_open():
            raise ChatProviderUnavailableError(retry_after_seconds=30)

        instructions = stable_system_instructions()
        grounding = build_grounding_message(
            context,
            chunks,
            intent=intent,
            meal_candidates=meal_candidates,
            earlier=earlier,
        )
        cache_kwargs = {}
        if self._cache_policy is not None:
            cache_kwargs = self._cache_policy.request_kwargs(
                model=self._model,
                purpose_hint="chat_coach",
                system_message=instructions,
            )

        first_token_at: float | None = None
        started = time.perf_counter()
        allergies = context.allergies or []
        last_error: Exception | None = None
        yielded_any = False
        stated_user_text = _stated_user_text(user_message, history)
        budget = output_token_budget or self._max_output_tokens

        for attempt in range(2):
            kept: list[str] = []
            pending = ""
            buffer = SentenceBuffer()
            generation["blocked"] = False
            generation["truncated"] = False
            try:
                async for delta in self._completion.stream(
                    model=self._model,
                    system_instructions=instructions,
                    grounding_message=grounding,
                    history=history,
                    user_message=user_message,
                    max_output_tokens=budget,
                    cache_kwargs=cache_kwargs,
                    tools=generation.get("tools"),
                ):
                    if delta.provider_response_id:
                        generation["provider_response_id"] = delta.provider_response_id
                    if delta.usage is not None:
                        generation["usage"] = delta.usage
                    if delta.truncated:
                        generation["truncated"] = True
                    if delta.tool_calls is not None:
                        generation["tool_calls"] = delta.tool_calls
                    if delta.text:
                        if first_token_at is None:
                            first_token_at = time.perf_counter()
                            distribution_metric(
                                "chat.turn.ttft_ms",
                                (first_token_at - started) * 1000,
                                unit="millisecond",
                            )
                        next_pending = _accept_stream_chunk(
                            emitted="".join(kept),
                            pending=pending,
                            chunk=delta.text,
                            allergies=allergies,
                            chunks=chunks,
                            context=context,
                            meal_candidates=traceable_meals
                            if traceable_meals is not None
                            else meal_candidates,
                            user_text=stated_user_text,
                        )
                        if next_pending is None:
                            generation["blocked"] = True
                            break
                        kept.append(delta.text)
                        pending = next_pending
                        for sentence in buffer.push(delta.text):
                            yielded_any = True
                            yield sentence
                        prefix = buffer.take_streamable_prefix()
                        if prefix:
                            yielded_any = True
                            yield prefix
                leftover = buffer.flush()
                if leftover:
                    yielded_any = True
                    yield leftover
                self._record_circuit_success()
                last_error = None
                generation["text"] = "".join(kept)
                break
            except Exception as exc:
                last_error = exc
                if (
                    attempt == 0
                    and not yielded_any
                    and _is_retryable_provider_error(exc)
                ):
                    self._record_circuit_failure()
                    increment_metric("chat.turn.provider_retry")
                    continue
                self._record_circuit_failure()
                raise ChatProviderUnavailableError(retryable=True) from exc

        if last_error is not None:
            raise ChatProviderUnavailableError(retryable=True) from last_error

        if not generation["text"].strip() and not generation.get("tool_calls"):
            # Reasoning can spend the whole budget before any visible token.
            # A fallback here would set blocked and skip the higher-cap retry.
            if generation.get("truncated"):
                return
            fallback = (
                no_evidence_message(context.locale)
                if not chunks and not generation["blocked"]
                else safe_fallback_message(context.locale)
            )
            generation["text"] = fallback
            generation["blocked"] = True
            yield fallback

    async def _complete(
        self,
        message: ChatMessage,
        *,
        content: str,
        usage: ChatUsage,
        citations: list[ChatCitation],
        provider_response_id: str | None,
        reply_payload: dict[str, Any] | None = None,
    ) -> ChatMessage | None:
        async with self._uow_factory() as uow:
            repo = _chat_repo(uow)
            return await repo.complete_assistant_message(
                message_id=message.id,
                content=content,
                model=self._model,
                usage=usage,
                prompt_version=CHAT_PROMPT_VERSION,
                context_version=CHAT_CONTEXT_VERSION,
                citation_source_keys=tuple(item.source_key for item in citations),
                provider_response_id=provider_response_id,
                reply_payload=reply_payload or empty_reply_payload(),
                generation_id=message.generation_id,
            )

    async def _generate_follow_ups(
        self,
        *,
        locale: str,
        intent: str | None,
        slot: str | None,
        user_message: str,
        assistant_text: str,
        has_suggestions: bool,
        suggestion_names: list[str] | None = None,
    ) -> list[dict[str, str]]:
        generated: list[dict[str, str]] = []
        names = suggestion_names or []
        if self._follow_ups is not None:
            try:
                generated = await asyncio.wait_for(
                    self._follow_ups.generate_follow_ups(
                        model=self._model,
                        locale=locale,
                        intent=intent,
                        slot=slot,
                        user_message=user_message,
                        assistant_text=assistant_text,
                        has_suggestions=has_suggestions,
                        suggestion_names=names,
                    ),
                    timeout=2.0,
                )
            except Exception:
                logger.info(
                    "chat follow-up generation failed", extra={"intent": intent}
                )
        return ensure_follow_ups(
            generated,
            locale=locale,
            intent=intent,
            slot=slot,
            has_suggestions=has_suggestions,
            suggestion_names=names,
        )

    async def _attach_meal_images(
        self, suggestions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        attach = getattr(self._next_meals, "attach_images", None)
        if attach is None:
            return suggestions
        try:
            updated = await attach(suggestions)
        except Exception:
            logger.warning("chat next-meal image attach failed", exc_info=True)
            return suggestions
        if not isinstance(updated, list):
            return suggestions
        return updated

    async def _store_meal_suggestions(
        self, message_id: str, suggestions: list[dict[str, Any]]
    ) -> None:
        try:
            async with self._uow_factory() as uow:
                repo = _chat_repo(uow)
                await repo.save_reply_suggestions(
                    message_id=message_id,
                    suggestions=suggestions,
                )
        except Exception:
            logger.info(
                "chat meal photo save failed",
                extra={"assistant_message_id": message_id},
            )

    async def _save_thread_summary(
        self, thread_id: str, summary: str, through_message_id: str
    ) -> None:
        try:
            async with self._uow_factory() as uow:
                repo = _chat_repo(uow)
                await repo.save_thread_summary(
                    thread_id=thread_id,
                    summary=summary,
                    through_message_id=through_message_id,
                )
        except Exception:
            logger.info(
                "chat thread summary save failed",
                extra={"thread_id": thread_id},
            )

    async def _attach_follow_ups(
        self, message_id: str, follow_ups: list[dict[str, str]]
    ) -> None:
        async with self._uow_factory() as uow:
            repo = _chat_repo(uow)
            await repo.attach_follow_ups(message_id=message_id, follow_ups=follow_ups)

    async def _save_partial_or_fail(
        self,
        claim: ChatTurnClaim,
        *,
        sentences: list[str],
        generation: dict[str, Any],
        suggestions: list[dict[str, Any]],
        discover_session_id: str | None,
        intent: str | None,
        error_code: str,
    ) -> None:
        """Keep text the client already saw, otherwise mark the turn failed."""
        partial = sanitize_incomplete_assistant_text("".join(sentences).strip())
        if partial:
            try:
                saved = await self._complete(
                    claim.assistant_message,
                    content=partial,
                    usage=generation["usage"],
                    citations=[],
                    provider_response_id=generation.get("provider_response_id"),
                    reply_payload=_reply_payload(
                        suggestions=suggestions,
                        follow_ups=[],
                        discover_session_id=discover_session_id,
                        intent=intent,
                    ),
                )
            except Exception:
                saved = None
            if saved is not None:
                return
            await self._fail(
                claim.assistant_message.id,
                error_code,
                generation_id=claim.assistant_message.generation_id,
                content=partial,
            )
            return
        await self._fail(
            claim.assistant_message.id,
            error_code,
            generation_id=claim.assistant_message.generation_id,
        )

    async def _fail(
        self,
        message_id: str,
        error_code: str,
        *,
        generation_id: str | None = None,
        content: str | None = None,
    ) -> None:
        try:
            async with self._uow_factory() as uow:
                repo = _chat_repo(uow)
                await repo.fail_assistant_message(
                    message_id=message_id,
                    error_code=error_code,
                    generation_id=generation_id,
                    content=content,
                )
        except Exception:
            logger.warning(
                "failed to persist chat error state",
                extra={"error_code": error_code, "assistant_message_id": message_id},
            )

    async def _try_acquire_slot(self) -> bool:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=2.0)
        except TimeoutError:
            return False
        return True

    def _circuit_is_open(self) -> bool:
        if self._circuit is None:
            return False
        state = self._circuit.get_state(self._model)
        value = getattr(state, "value", state)
        return str(value).lower() == "open"

    def _record_circuit_success(self) -> None:
        if self._circuit is not None:
            self._circuit.record_success(self._model)

    def _record_circuit_failure(self) -> None:
        if self._circuit is not None:
            self._circuit.record_failure(self._model)


def _chat_repo(uow: AsyncUnitOfWorkPort) -> ChatRepositoryPort:
    repo = getattr(uow, "chat", None)
    if repo is None:
        raise RuntimeError("Unit of work is missing chat repository")
    return repo


def _started_event(claim: ChatTurnClaim) -> ChatSseEvent:
    return ChatSseEvent(
        event="message.started",
        data={
            "thread_id": claim.thread.id,
            "user_message_id": claim.user_message.id,
            "assistant_message_id": claim.assistant_message.id,
        },
    )


def _cards_named_in_reply(
    reply: str, cards: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The card the answer names, so Log stays on that dish."""
    folded = reply.casefold()
    matched = [
        card
        for card in cards
        if str(card.get("name") or "").strip()
        and str(card.get("name")).strip().casefold() in folded
    ]
    return matched or cards[:1]


def _shown_meal_cards(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Named cards from the newest assistant turn that showed meals."""
    for message in reversed(messages):
        if message.role != ChatMessageRole.ASSISTANT:
            continue
        cards = [
            card
            for card in message.suggestions()
            if isinstance(card, dict) and str(card.get("name") or "").strip()
        ]
        if cards:
            return cards
    return []


def _latest_shown_meal_names(messages: list[ChatMessage]) -> list[str]:
    """Dish names from the newest assistant turn that showed meal cards."""
    for message in reversed(messages):
        if message.role != ChatMessageRole.ASSISTANT:
            continue
        names = [
            str(card.get("name")).strip()
            for card in message.suggestions()
            if isinstance(card, dict) and str(card.get("name") or "").strip()
        ]
        if names:
            return names[:3]
    return []


def _meal_revision_text(user_text: str, prior_names: list[str]) -> str:
    if not prior_names:
        return user_text
    shown = ", ".join(prior_names)
    return f"{user_text}. Revise these meals: {shown}."


def _meal_card_history_note(card: dict[str, Any]) -> str:
    name = str(card.get("name") or "").strip()
    note = f"Meal card shown: {name}."
    steps = _recipe_step_note(card)
    if steps:
        note = f"{note} {steps}"
    calories = _finite_number(card.get("calories"))
    if calories is None:
        return note
    parts = [f"{_format_amount(calories)} kcal"]
    for key, label in (
        ("protein_g", "protein"),
        ("carbs_g", "carbs"),
        ("fat_g", "fat"),
    ):
        value = _finite_number(card.get(key))
        if value is None:
            continue
        parts.append(f"{_format_amount(value)}g {label}")
    return f"{note} Authoritative portion: {', '.join(parts)}."


def _recipe_step_note(card: dict[str, Any]) -> str:
    """Short cooking steps from the card, so a how-to answer is not invented."""
    raw = card.get("recipe_steps")
    if not isinstance(raw, list):
        return ""
    lines: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            text = item.get("instruction") or item.get("text") or ""
        elif isinstance(item, str):
            text = item
        else:
            continue
        cleaned = " ".join(str(text).split())
        if not cleaned:
            continue
        lines.append(cleaned[:120])
        if len(lines) >= 3:
            break
    if not lines:
        return ""
    numbered = " ".join(f"{index}. {step}" for index, step in enumerate(lines, start=1))
    return f"Recipe steps: {numbered}"


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _format_amount(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)


def _traceable_meals(
    current: list[dict[str, Any]],
    history: list[ChatMessage],
) -> list[dict[str, Any]]:
    """Current cards plus earlier meal cards, so a follow-up can quote them."""
    seen = {id(card) for card in current}
    merged = list(current)
    for message in history:
        for card in message.suggestions():
            if not isinstance(card, dict) or card.get("calories") is None:
                continue
            if id(card) in seen:
                continue
            seen.add(id(card))
            merged.append(card)
    return merged


def _history_turns(messages: list[ChatMessage]) -> list[ChatHistoryTurn]:
    turns: list[ChatHistoryTurn] = []
    for message in messages:
        content = (message.content or "").strip()
        if message.role == ChatMessageRole.ASSISTANT:
            notes = [
                _meal_card_history_note(card)
                for card in message.suggestions()
                if isinstance(card, dict) and str(card.get("name") or "").strip()
            ][:3]
            missing = [note for note in notes if note not in content]
            if missing:
                content = f"{content}\n" + "\n".join(missing)
                content = content.strip()
        if not content:
            continue
        turns.append(ChatHistoryTurn(role=message.role, content=content))
    return turns[-CHAT_HISTORY_LIMIT:]


def _reply_payload(
    *,
    suggestions: list[dict[str, Any]],
    follow_ups: list[dict[str, str]],
    discover_session_id: str | None,
    intent: str | None,
    citations: list[ChatCitation] | None = None,
    nutrition_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "suggestions": suggestions,
        "follow_ups": follow_ups,
    }
    if discover_session_id:
        payload["discover_session_id"] = discover_session_id
    if intent:
        payload["intent"] = intent
    if citations:
        payload["citation_refs"] = [
            {"label": item.label, "source_key": item.source_key} for item in citations
        ]
    if nutrition_snapshot is not None:
        payload["nutrition_snapshot"] = nutrition_snapshot
    return payload


def _daily_progress_unavailable(locale: str) -> str:
    if locale == "vi":
        return (
            "Nutree chưa thể tải đủ số liệu hôm nay. "
            "Vui lòng thử lại sau khi dữ liệu được đồng bộ."
        )
    return (
        "Nutree cannot load complete nutrition data for today yet. "
        "Please try again after your data finishes syncing."
    )


def _hydrate_message_citations(
    message: ChatMessage,
    metadata: dict[str, tuple[str | None, str | None]] | None = None,
) -> list[dict[str, str | None]]:
    refs = message.citation_refs()
    keys = [key for _, key in refs] if refs else list(message.citation_source_keys)
    labels = [label for label, _ in refs] if refs else None
    return hydrate_citations(keys, metadata or {}, labels=labels)


def _completed_sidecar(
    message: ChatMessage,
    follow_ups: list[dict[str, str]],
    *,
    suggestions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = reply_sidecar(message)
    if follow_ups:
        payload["follow_ups"] = follow_ups
    if suggestions:
        payload["suggestions"] = suggestions
    return payload


def _thread_payload(thread: ChatThread) -> dict[str, Any]:
    return {
        "id": thread.id,
        "created_at": thread.created_at.isoformat(),
        "updated_at": thread.updated_at.isoformat(),
    }


def _thread_listing_payload(listing: ChatThreadListing) -> dict[str, Any]:
    last = listing.last_message_at
    return {
        **_thread_payload(listing.thread),
        "title": listing.title,
        "preview": listing.preview,
        "preview_role": listing.preview_role.value if listing.preview_role else None,
        "last_message_at": last.isoformat() if last else None,
    }


def _public_message(
    message: ChatMessage,
    citations: list[dict[str, str | None]] | None = None,
) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role.value,
        "status": message.status.value,
        "content": message.content,
        "created_at": message.created_at.isoformat(),
        "model": message.model,
        "citation_source_keys": list(message.citation_source_keys),
        "citations": citations or _hydrate_message_citations(message),
        **reply_sidecar(message),
    }


def _in_flight_payload(
    generating: tuple[ChatMessage, ChatMessage] | None,
) -> dict[str, Any] | None:
    if generating is None:
        return None
    user_message, assistant_message = generating
    lease = assistant_message.generation_lease_expires_at
    return {
        "user_message": _public_message(user_message),
        "assistant_message_id": assistant_message.id,
        "idempotency_key": user_message.idempotency_key,
        "lease_expires_at": lease.isoformat() if lease else None,
    }


def _citations_for(
    text: str,
    chunks: list[RetrievedKnowledgeChunk],
) -> list[ChatCitation]:
    wanted = set(cited_labels(text))
    citations: list[ChatCitation] = []
    for chunk in chunks:
        if chunk.label in wanted:
            citations.append(
                ChatCitation(
                    label=chunk.label,
                    source_key=chunk.source_key,
                    title=chunk.title,
                    canonical_uri=chunk.canonical_uri,
                    score=chunk.fused_score,
                )
            )
    return citations


def _stated_user_text(user_message: str, history: list[ChatHistoryTurn]) -> str:
    """User turns only, so a follow-up like "Hả" still carries the stated 200 kcal."""
    parts = [
        turn.content
        for turn in history
        if turn.role == ChatMessageRole.USER and turn.content
    ]
    if user_message:
        parts.append(user_message)
    return "\n".join(parts)


def _combine_chat_usage(first: ChatUsage | None, second: ChatUsage | None) -> ChatUsage:
    """Add a higher-cap retry's tokens onto the truncated first pass."""
    left = first or ChatUsage()
    if second is None:
        return left
    return ChatUsage(
        input_tokens=left.input_tokens + second.input_tokens,
        output_tokens=left.output_tokens + second.output_tokens,
        cached_tokens=left.cached_tokens + second.cached_tokens,
        model=second.model or left.model,
    )


def _should_retry_output_limit(
    generation: dict[str, Any], sentences: list[str]
) -> bool:
    """One more pass when reasoning tokens ate the visible answer."""
    if generation.get("output_limit_retried"):
        return False
    if generation.get("blocked") or generation.get("tool_calls"):
        return False
    if not generation.get("truncated"):
        return False
    text = "".join(sentences).strip()
    # No visible text still hit the cap (reasoning-only). Retry that too.
    if not text:
        return True
    return finished_assistant_text(text) != text


def _accept_stream_chunk(
    *,
    emitted: str,
    pending: str,
    chunk: str,
    allergies: Iterable[str],
    chunks: list[RetrievedKnowledgeChunk],
    context: ChatUserContext,
    meal_candidates: list[dict[str, Any]] | None,
    user_text: str = "",
) -> str | None:
    """Return the new unfinished sentence if `chunk` is safe to stream."""
    tentative = emitted + chunk
    if not citations_are_valid(tentative, chunks):
        return None
    if not nutrition_numbers_are_traceable(
        tentative,
        context=context,
        chunks=chunks,
        meal_candidates=meal_candidates,
        user_text=user_text,
    ):
        return None
    splitter = SentenceBuffer()
    completed = splitter.push(pending + chunk)
    leftover = splitter.flush()
    for sentence in completed:
        if not inspect_sentence(sentence, allergies=allergies).allowed:
            return None
    if leftover and not inspect_sentence(leftover, allergies=allergies).allowed:
        return None
    return leftover


def _is_retryable_provider_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _RETRYABLE_PROVIDER_MARKERS)
