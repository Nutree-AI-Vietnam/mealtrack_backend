"""Persistence port for the single-thread chat coach."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from src.domain.model.chat import (
    ChatMessage,
    ChatThread,
    ChatTurnClaim,
    ChatUsage,
)


class ChatRepositoryPort(ABC):
    """Owns thread/message persistence and turn-claim invariants."""

    @abstractmethod
    async def get_or_create_thread(self, user_id: str) -> ChatThread:
        """Return the unique thread for the user, creating it if needed."""

    @abstractmethod
    async def get_thread(self, user_id: str) -> ChatThread | None:
        """Return the user's thread without creating one."""

    @abstractmethod
    async def claim_turn(
        self,
        *,
        user_id: str,
        content: str,
        idempotency_key: str,
        request_fingerprint: str,
        lease_expires_at: datetime,
    ) -> ChatTurnClaim:
        """Persist the user message and a generating assistant placeholder."""

    @abstractmethod
    async def list_completed_messages(
        self,
        *,
        thread_id: str,
        limit: int,
        before_message_id: str | None = None,
    ) -> list[ChatMessage]:
        """Return completed messages newest-first, optionally before a cursor."""

    @abstractmethod
    async def list_recent_completed_history(
        self,
        *,
        thread_id: str,
        limit: int,
    ) -> list[ChatMessage]:
        """Return the latest completed messages oldest-first for model history."""

    @abstractmethod
    async def get_generating_turn(
        self, thread_id: str
    ) -> tuple[ChatMessage, ChatMessage] | None:
        """Return the in-flight user and assistant messages, if any."""

    @abstractmethod
    async def list_citation_metadata(
        self, source_keys: Sequence[str]
    ) -> dict[str, tuple[str | None, str | None]]:
        """Map source keys to (title, canonical_uri) from reviewed knowledge."""

    @abstractmethod
    async def complete_assistant_message(
        self,
        *,
        message_id: str,
        content: str,
        model: str,
        usage: ChatUsage,
        prompt_version: str,
        context_version: str,
        citation_source_keys: tuple[str, ...],
        provider_response_id: str | None,
        reply_payload: dict[str, Any] | None = None,
        generation_id: str | None = None,
    ) -> ChatMessage | None:
        """Complete the generating assistant only if the fencing token still owns it."""

    @abstractmethod
    async def fail_assistant_message(
        self,
        *,
        message_id: str,
        error_code: str,
        content: str | None = None,
        generation_id: str | None = None,
    ) -> ChatMessage | None:
        """Fail the generating assistant only if it is still the active generation."""

    @abstractmethod
    async def count_user_turns_since(
        self,
        *,
        user_id: str,
        since: datetime,
        exclude_idempotency_key: str | None = None,
    ) -> int:
        """Count user messages created at or after *since*."""

    @abstractmethod
    async def clear_thread(self, user_id: str) -> ChatThread:
        """Delete messages and summary while preserving the one-thread identity."""

    @abstractmethod
    async def delete_user_chat(self, user_id: str) -> None:
        """Remove the thread and messages during account deletion."""
