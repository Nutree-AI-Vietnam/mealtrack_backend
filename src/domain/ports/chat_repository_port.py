"""Persistence port for the multi-thread chat coach."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from src.domain.model.chat import (
    ChatMessage,
    ChatThread,
    ChatThreadListing,
    ChatTurnClaim,
    ChatUsage,
)


class ChatRepositoryPort(ABC):
    """Owns thread/message persistence and turn-claim invariants."""

    @abstractmethod
    async def get_or_create_thread(self, user_id: str) -> ChatThread:
        """Return the user's most recently active thread, creating one if none."""

    @abstractmethod
    async def get_thread(
        self, user_id: str, thread_id: str | None = None
    ) -> ChatThread | None:
        """Return the user's thread without creating one.

        With *thread_id*, only that thread and only if the user owns it;
        otherwise the most recently active thread.
        """

    @abstractmethod
    async def create_thread(self, user_id: str) -> ChatThread:
        """Start a new thread, reusing the user's empty thread if one exists."""

    @abstractmethod
    async def list_threads(
        self,
        *,
        user_id: str,
        limit: int,
        before_thread_id: str | None = None,
    ) -> list[ChatThreadListing]:
        """Return threads with messages, most recently active first."""

    @abstractmethod
    async def claim_turn(
        self,
        *,
        user_id: str,
        content: str,
        idempotency_key: str,
        request_fingerprint: str,
        lease_expires_at: datetime,
        thread_id: str | None = None,
    ) -> ChatTurnClaim:
        """Persist the user message and a generating assistant placeholder.

        Without *thread_id* the turn goes to the most recently active thread.
        Raises ChatThreadNotFoundError when *thread_id* is not the user's.
        """

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
    async def attach_follow_ups(
        self,
        *,
        message_id: str,
        follow_ups: list[dict[str, Any]],
    ) -> None:
        """Store chips on an already completed assistant without reopening it."""

    @abstractmethod
    async def save_reply_suggestions(
        self,
        *,
        message_id: str,
        suggestions: list[dict[str, Any]],
    ) -> None:
        """Replace meal cards on a completed reply, including photos added later."""

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
    async def reopen_assistant_message(
        self,
        *,
        user_id: str,
        assistant_message_id: str,
        lease_expires_at: datetime,
        content: str | None = None,
        thread_id: str | None = None,
    ) -> tuple[ChatTurnClaim, str | None, bool] | None:
        """Reopen the latest completed assistant so it can be generated again.

        The message must sit in one of the user's threads, and in *thread_id*
        when that is given.

        Returns the claim, the previous reply intent, and whether the stored
        question text changed. None when the message cannot be regenerated.
        """

    @abstractmethod
    async def count_user_turns_since(
        self,
        *,
        user_id: str,
        since: datetime,
        exclude_idempotency_key: str | None = None,
    ) -> int:
        """Count user messages created at or after *since*, across all threads."""

    @abstractmethod
    async def save_thread_summary(
        self,
        *,
        thread_id: str,
        summary: str,
        through_message_id: str,
    ) -> None:
        """Remember turns that have fallen out of the live history window."""

    @abstractmethod
    async def clear_thread(
        self, user_id: str, thread_id: str | None = None
    ) -> ChatThread:
        """Delete one thread's messages and summary, keeping the thread itself.

        Without *thread_id* this clears the most recently active thread.
        """

    @abstractmethod
    async def delete_user_chat(self, user_id: str) -> None:
        """Remove every thread and message during account deletion."""
