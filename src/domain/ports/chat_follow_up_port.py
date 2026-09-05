"""Structured follow-up chips after a completed chat turn."""

from __future__ import annotations

from abc import ABC, abstractmethod


class ChatFollowUpPort(ABC):
    """Tiny structured-output call. Must not store conversation state."""

    @abstractmethod
    async def generate_follow_ups(
        self,
        *,
        model: str,
        locale: str,
        intent: str | None,
        slot: str | None,
        user_message: str,
        assistant_text: str,
        has_suggestions: bool,
    ) -> list[dict[str, str]]:
        """Return 0–3 {label, action} chips. Empty list on failure."""
