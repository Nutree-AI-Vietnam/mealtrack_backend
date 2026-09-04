"""Embedding port for chat knowledge retrieval."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence


class ChatEmbeddingPort(ABC):
    """Embed query text for hybrid retrieval. Never embed user PII into the corpus."""

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Return a single query embedding vector."""

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed many strings. Default loops; adapters may batch."""
        return [await self.embed_query(text) for text in texts]
