"""OpenAI embeddings adapter for chat knowledge queries."""

from __future__ import annotations

from openai import AsyncOpenAI

from src.domain.ports.chat_embedding_port import ChatEmbeddingPort


class OpenAIChatEmbeddingAdapter(ChatEmbeddingPort):
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "text-embedding-3-small",
        timeout_seconds: float = 10.0,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds)
        self._model = model

    async def embed_query(self, text: str) -> list[float]:
        response = await self._client.embeddings.create(
            model=self._model,
            input=text,
        )
        return list(response.data[0].embedding)
