"""Embed typed chat and pick a ChatIntent. Phrase match first, then cosine."""

from __future__ import annotations

import asyncio
import logging

from src.domain.ports.chat_embedding_port import ChatEmbeddingPort
from src.domain.services.chat.intent_classification import (
    INTENT_EXEMPLARS,
    IntentDecision,
    phrase_intent,
    pick_intent,
    score_intents,
)
from src.domain.services.chat.topic_scope import (
    SCOPE_EXEMPLARS,
    SCOPE_IN,
    ScopeDecision,
    phrase_scope,
    pick_scope,
)
from src.observability import increment_metric, log_event

logger = logging.getLogger(__name__)


class ChatIntentClassifier:
    """Server-only. Client-sent chip intents must not go through this."""

    def __init__(self, embedding: ChatEmbeddingPort) -> None:
        self._embedding = embedding
        self._exemplars: dict[str, list[list[float]]] | None = None
        self._scope_exemplars: dict[str, list[list[float]]] | None = None
        self._lock = asyncio.Lock()

    async def classify(
        self,
        text: str,
        *,
        query_embedding: list[float] | None = None,
    ) -> IntentDecision:
        phrase = phrase_intent(text)
        if phrase:
            decision = IntentDecision(phrase, "phrase", {})
            _record(decision)
            return decision

        embedding = query_embedding
        if not embedding:
            try:
                embedding = await self._embedding.embed_query(text)
            except Exception:
                log_event(
                    "warning",
                    "chat intent embedding failed",
                    attributes={"error_code": "CHAT_INTENT_EMBEDDING_FAILED"},
                )
                decision = IntentDecision(None, "none", {})
                _record(decision)
                return decision

        exemplars = await self._exemplar_embeddings()
        if not exemplars:
            decision = IntentDecision(None, "none", {})
            _record(decision)
            return decision

        decision = pick_intent(score_intents(embedding, exemplars))
        _record(decision)
        return decision

    async def classify_scope(
        self,
        text: str,
        *,
        query_embedding: list[float] | None = None,
    ) -> ScopeDecision:
        phrase = phrase_scope(text)
        if phrase:
            decision = ScopeDecision(phrase, "phrase", {})
            _record_scope(decision)
            return decision

        embedding = query_embedding
        if not embedding:
            try:
                embedding = await self._embedding.embed_query(text)
            except Exception:
                log_event(
                    "warning",
                    "chat scope embedding failed",
                    attributes={"error_code": "CHAT_SCOPE_EMBEDDING_FAILED"},
                )
                decision = ScopeDecision(SCOPE_IN, "default", {})
                _record_scope(decision)
                return decision

        exemplars = await self._scope_exemplar_embeddings()
        if not exemplars:
            decision = ScopeDecision(SCOPE_IN, "default", {})
            _record_scope(decision)
            return decision

        decision = pick_scope(score_intents(embedding, exemplars))
        _record_scope(decision)
        return decision

    async def _exemplar_embeddings(self) -> dict[str, list[list[float]]]:
        if self._exemplars is not None:
            return self._exemplars
        async with self._lock:
            if self._exemplars is not None:
                return self._exemplars
            phrases: list[str] = []
            owners: list[str] = []
            for intent, examples in INTENT_EXEMPLARS.items():
                for example in examples:
                    phrases.append(example)
                    owners.append(intent)
            try:
                vectors = await self._embedding.embed_texts(phrases)
            except Exception:
                log_event(
                    "warning",
                    "chat intent exemplar embed failed",
                    attributes={"error_code": "CHAT_INTENT_EXEMPLAR_FAILED"},
                )
                return {}
            if len(vectors) != len(owners):
                return {}
            grouped: dict[str, list[list[float]]] = {
                key: [] for key in INTENT_EXEMPLARS
            }
            for intent, vector in zip(owners, vectors, strict=True):
                if vector:
                    grouped[intent].append(vector)
            self._exemplars = grouped
            return grouped

    async def _scope_exemplar_embeddings(self) -> dict[str, list[list[float]]]:
        if self._scope_exemplars is not None:
            return self._scope_exemplars
        async with self._lock:
            if self._scope_exemplars is not None:
                return self._scope_exemplars
            phrases: list[str] = []
            owners: list[str] = []
            for scope, examples in SCOPE_EXEMPLARS.items():
                for example in examples:
                    phrases.append(example)
                    owners.append(scope)
            try:
                vectors = await self._embedding.embed_texts(phrases)
            except Exception:
                log_event(
                    "warning",
                    "chat scope exemplar embed failed",
                    attributes={"error_code": "CHAT_SCOPE_EXEMPLAR_FAILED"},
                )
                return {}
            if len(vectors) != len(owners):
                return {}
            grouped: dict[str, list[list[float]]] = {key: [] for key in SCOPE_EXEMPLARS}
            for scope, vector in zip(owners, vectors, strict=True):
                if vector:
                    grouped[scope].append(vector)
            self._scope_exemplars = grouped
            return grouped


def _record(decision: IntentDecision) -> None:
    increment_metric(
        "chat.intent.classified",
        attributes={
            "intent": decision.intent or "none",
            "source": decision.source,
        },
    )
    logger.info(
        "chat intent classified",
        extra={
            "intent": decision.intent,
            "source": decision.source,
            "margin": round(decision.margin, 4),
        },
    )


def _record_scope(decision: ScopeDecision) -> None:
    increment_metric(
        "chat.scope.classified",
        attributes={"scope": decision.scope, "source": decision.source},
    )
    logger.info(
        "chat scope classified",
        extra={
            "scope": decision.scope,
            "source": decision.source,
            "margin": round(decision.margin, 4),
        },
    )
