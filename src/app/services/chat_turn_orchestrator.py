"""Chat turn orchestration: claim, ground, generate, validate, persist, SSE events."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from src.app.services.chat_intent_classifier import ChatIntentClassifier
from src.app.services.chat_next_meal_candidates import (
    ChatNextMealCandidates,
    last_discover_session_id,
)
from src.domain.exceptions.chat_exceptions import (
    ChatProviderUnavailableError,
    ChatRateLimitedError,
)
from src.domain.model.chat import (
    CHAT_CONTEXT_VERSION,
    CHAT_HISTORY_LIMIT,
    CHAT_MAX_OUTPUT_TOKENS,
    CHAT_MAX_USER_MESSAGE_CHARS,
    CHAT_PROMPT_VERSION,
    CHAT_RETRIEVAL_MAX_CHUNKS,
    ChatCitation,
    ChatClaimKind,
    ChatHistoryTurn,
    ChatIntent,
    ChatMessage,
    ChatSseEvent,
    ChatTurnClaim,
    ChatUsage,
    ChatUserContext,
    RetrievedKnowledgeChunk,
    empty_reply_payload,
    reply_sidecar,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.chat_completion_port import ChatCompletionPort
from src.domain.ports.chat_embedding_port import ChatEmbeddingPort
from src.domain.ports.chat_follow_up_port import ChatFollowUpPort
from src.domain.ports.chat_knowledge_retrieval_port import ChatKnowledgeRetrievalPort
from src.domain.ports.chat_repository_port import ChatRepositoryPort
from src.domain.services.chat.meal_slot import resolve_meal_slot
from src.domain.services.chat.policy import (
    SentenceBuffer,
    build_grounding_message,
    citations_are_valid,
    cited_labels,
    filter_chunks_for_allergies,
    hydrate_citations,
    inspect_sentence,
    no_evidence_message,
    nutrition_numbers_are_traceable,
    out_of_scope_follow_ups,
    out_of_scope_message,
    request_fingerprint,
    resolve_chat_locale,
    safe_fallback_message,
    stable_system_instructions,
)
from src.domain.services.chat.topic_scope import SCOPE_OUT
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
    slot_acquired: bool = False


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
        intent_classifier: ChatIntentClassifier | None = None,
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
        self._max_output_tokens = max_output_tokens
        self._semaphore = semaphore or asyncio.Semaphore(max(1, global_concurrency))
        self._circuit = circuit_breaker
        self._next_meals = next_meals
        self._follow_ups = follow_ups
        self._intent_classifier = intent_classifier or ChatIntentClassifier(embedding)

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
    ) -> PreparedChatTurn:
        started = time.perf_counter()
        resolved_locale = resolve_chat_locale(locale, user_language)
        trimmed = content.strip()
        if len(trimmed) > CHAT_MAX_USER_MESSAGE_CHARS:
            trimmed = trimmed[:CHAT_MAX_USER_MESSAGE_CHARS]
        fingerprint = request_fingerprint(trimmed, resolved_locale, intent)
        await self._enforce_daily_budget(user_id)
        claim = await self._claim(
            user_id=user_id,
            content=trimmed,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        )
        slot_acquired = False
        if claim.kind != ChatClaimKind.REPLAY:
            if self._circuit_is_open():
                await self._fail(
                    claim.assistant_message.id, "CHAT_PROVIDER_UNAVAILABLE"
                )
                raise ChatProviderUnavailableError(
                    retry_after_seconds=30, retryable=True
                )
            slot_acquired = await self._try_acquire_slot()
            if not slot_acquired:
                await self._fail(
                    claim.assistant_message.id, "CHAT_PROVIDER_UNAVAILABLE"
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

            context, chunks, query_embedding = await self._ground(
                user_id=user_id,
                query=trimmed,
                locale=resolved_locale,
                header_timezone=header_timezone,
            )
            if intent is None:
                decision = await self._intent_classifier.classify(
                    trimmed, query_embedding=query_embedding
                )
                intent = decision.intent
            out_of_scope = False
            if intent is None:
                scope = await self._intent_classifier.classify_scope(
                    trimmed, query_embedding=query_embedding
                )
                out_of_scope = scope.scope == SCOPE_OUT
            history_messages = await self._completed_history(
                claim.thread.id, exclude_message_id=claim.user_message.id
            )
            history = _history_turns(history_messages)
            suggestions: list[dict[str, Any]] = []
            discover_session_id = last_discover_session_id(history_messages)
            meal_slot = resolve_meal_slot(context.suggested_meal_slot, trimmed)
            if (
                not out_of_scope
                and intent == ChatIntent.NEXT_MEAL.value
                and self._next_meals is not None
            ):
                batch = await self._next_meals.fetch(
                    user_id=user_id,
                    context=context,
                    user_text=trimmed,
                    locale=resolved_locale,
                    session_id=discover_session_id,
                )
                suggestions = batch.suggestions
                discover_session_id = batch.session_id or discover_session_id
                meal_slot = batch.meal_slot

            generation = {
                "text": "",
                "usage": ChatUsage(model=self._model),
                "provider_response_id": None,
                "blocked": False,
            }
            sentences: list[str] = []
            if out_of_scope:
                final_text = out_of_scope_message(resolved_locale)
                yield ChatSseEvent(
                    event="message.delta",
                    data={
                        "assistant_message_id": claim.assistant_message.id,
                        "delta": final_text,
                    },
                )
                usage = generation["usage"]
                provider_response_id = None
                blocked = False
                citations = []
                follow_ups = out_of_scope_follow_ups(resolved_locale)
            else:
                async for delta_text in self._iter_validated_sentences(
                    context=context,
                    chunks=chunks,
                    history=history,
                    user_message=trimmed,
                    generation=generation,
                    intent=intent,
                    meal_candidates=suggestions,
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

                final_text = "".join(sentences).strip()
                usage = generation["usage"]
                provider_response_id = generation["provider_response_id"]
                blocked = bool(generation["blocked"])
                if not final_text:
                    blocked = True
                    final_text = safe_fallback_message(resolved_locale)
                    yield ChatSseEvent(
                        event="message.delta",
                        data={
                            "assistant_message_id": claim.assistant_message.id,
                            "delta": final_text,
                        },
                    )

                citations = _citations_for(final_text, chunks)
                follow_ups = await self._generate_follow_ups(
                    locale=resolved_locale,
                    intent=intent,
                    slot=meal_slot,
                    user_message=trimmed,
                    assistant_text=final_text,
                    has_suggestions=bool(suggestions),
                )
            completed = await self._complete(
                claim.assistant_message.id,
                content=final_text,
                usage=usage,
                citations=citations,
                provider_response_id=provider_response_id,
                reply_payload=_reply_payload(
                    suggestions=suggestions,
                    follow_ups=follow_ups,
                    discover_session_id=discover_session_id,
                    intent=intent,
                ),
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
                    **reply_sidecar(completed),
                },
            )
        except ChatProviderUnavailableError as exc:
            await self._fail(claim.assistant_message.id, "CHAT_PROVIDER_UNAVAILABLE")
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
            await self._fail(claim.assistant_message.id, "CHAT_TURN_FAILED")
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
    ) -> dict[str, Any]:
        async with self._uow_factory() as uow:
            repo = _chat_repo(uow)
            thread = await repo.get_or_create_thread(user_id)
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
        chronological = list(reversed(page))
        return {
            "thread": {
                "id": thread.id,
                "created_at": thread.created_at.isoformat(),
                "updated_at": thread.updated_at.isoformat(),
            },
            "messages": [
                _public_message(
                    message,
                    hydrate_citations(message.citation_source_keys, citation_metadata),
                )
                for message in chronological
            ],
            "has_more": has_more,
            "in_flight": _in_flight_payload(generating),
        }

    async def clear_thread(self, user_id: str) -> dict[str, Any]:
        async with self._uow_factory() as uow:
            repo = _chat_repo(uow)
            thread = await repo.clear_thread(user_id)
        increment_metric("chat.thread.cleared")
        return {"thread_id": thread.id, "cleared": True}

    async def _enforce_daily_budget(self, user_id: str) -> None:
        start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
        async with self._uow_factory() as uow:
            repo = _chat_repo(uow)
            used = await repo.count_user_turns_since(user_id=user_id, since=start)
        if used >= self._daily_turn_budget:
            raise ChatRateLimitedError(retry_after_seconds=3600, daily=True)

    async def _claim(
        self,
        *,
        user_id: str,
        content: str,
        idempotency_key: str,
        request_fingerprint: str,
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

    async def _citations_for_message(
        self, message: ChatMessage
    ) -> list[dict[str, str | None]]:
        keys = list(message.citation_source_keys)
        if not keys:
            return []
        async with self._uow_factory() as uow:
            metadata = await _chat_repo(uow).list_citation_metadata(keys)
        return hydrate_citations(keys, metadata)

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
                limit=CHAT_HISTORY_LIMIT + 1,
            )
        return [message for message in messages if message.id != exclude_message_id]

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
    ) -> AsyncIterator[str]:
        if self._circuit_is_open():
            raise ChatProviderUnavailableError(retry_after_seconds=30)

        instructions = stable_system_instructions()
        grounding = build_grounding_message(
            context, chunks, intent=intent, meal_candidates=meal_candidates
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

        for attempt in range(2):
            kept: list[str] = []
            pending = ""
            generation["blocked"] = False
            try:
                async for delta in self._completion.stream(
                    model=self._model,
                    system_instructions=instructions,
                    grounding_message=grounding,
                    history=history,
                    user_message=user_message,
                    max_output_tokens=self._max_output_tokens,
                    cache_kwargs=cache_kwargs,
                ):
                    if delta.provider_response_id:
                        generation["provider_response_id"] = delta.provider_response_id
                    if delta.usage is not None:
                        generation["usage"] = delta.usage
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
                            meal_candidates=meal_candidates,
                        )
                        if next_pending is None:
                            generation["blocked"] = True
                            continue
                        kept.append(delta.text)
                        pending = next_pending
                        yielded_any = True
                        yield delta.text
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

        if not generation["text"].strip():
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
        message_id: str,
        *,
        content: str,
        usage: ChatUsage,
        citations: list[ChatCitation],
        provider_response_id: str | None,
        reply_payload: dict[str, Any] | None = None,
    ) -> ChatMessage:
        async with self._uow_factory() as uow:
            repo = _chat_repo(uow)
            return await repo.complete_assistant_message(
                message_id=message_id,
                content=content,
                model=self._model,
                usage=usage,
                prompt_version=CHAT_PROMPT_VERSION,
                context_version=CHAT_CONTEXT_VERSION,
                citation_source_keys=tuple(item.source_key for item in citations),
                provider_response_id=provider_response_id,
                reply_payload=reply_payload or empty_reply_payload(),
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
    ) -> list[dict[str, str]]:
        if self._follow_ups is None:
            return []
        try:
            return await asyncio.wait_for(
                self._follow_ups.generate_follow_ups(
                    model=self._model,
                    locale=locale,
                    intent=intent,
                    slot=slot,
                    user_message=user_message,
                    assistant_text=assistant_text,
                    has_suggestions=has_suggestions,
                ),
                timeout=2.0,
            )
        except Exception:
            logger.info("chat follow-up generation failed", extra={"intent": intent})
            return []

    async def _fail(self, message_id: str, error_code: str) -> None:
        try:
            async with self._uow_factory() as uow:
                repo = _chat_repo(uow)
                await repo.fail_assistant_message(
                    message_id=message_id, error_code=error_code
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


def _history_turns(messages: list[ChatMessage]) -> list[ChatHistoryTurn]:
    turns: list[ChatHistoryTurn] = []
    for message in messages:
        if not message.content:
            continue
        turns.append(ChatHistoryTurn(role=message.role, content=message.content))
    return turns[-CHAT_HISTORY_LIMIT:]


def _reply_payload(
    *,
    suggestions: list[dict[str, Any]],
    follow_ups: list[dict[str, str]],
    discover_session_id: str | None,
    intent: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "suggestions": suggestions,
        "follow_ups": follow_ups,
    }
    if discover_session_id:
        payload["discover_session_id"] = discover_session_id
    if intent:
        payload["intent"] = intent
    return payload


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
        "citations": citations or hydrate_citations(message.citation_source_keys, {}),
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


def _accept_stream_chunk(
    *,
    emitted: str,
    pending: str,
    chunk: str,
    allergies: Iterable[str],
    chunks: list[RetrievedKnowledgeChunk],
    context: ChatUserContext,
    meal_candidates: list[dict[str, Any]] | None,
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
