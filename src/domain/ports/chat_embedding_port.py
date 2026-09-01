"""Embedding port for chat knowledge retrieval."""

from __future__ import annotations

from abc import ABC, abstractmethod


class ChatEmbeddingPort(ABC):
    """Embed query text for hybrid retrieval. Never embed user PII into the corpus."""

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Return a single query embedding vector."""
