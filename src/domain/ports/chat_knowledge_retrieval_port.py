"""Retrieval port over reviewed Nutree knowledge only."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.model.chat import RetrievedKnowledgeChunk


class ChatKnowledgeRetrievalPort(ABC):
    """Hybrid search over versioned, human-reviewed Nutree documents."""

    @abstractmethod
    async def retrieve(
        self,
        *,
        query: str,
        query_embedding: list[float] | None,
        locale: str,
        allergies: list[str],
        limit: int,
    ) -> list[RetrievedKnowledgeChunk]:
        """Return labeled, de-duplicated chunks or an empty list when evidence is weak."""
