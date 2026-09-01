"""Streaming completion port for chat turns."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from src.domain.model.chat import ChatCompletionDelta, ChatHistoryTurn


class ChatCompletionPort(ABC):
    """Stateless provider completion. Must not store conversation state."""

    @abstractmethod
    def stream(
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
        """Yield text deltas then a terminal usage delta."""
