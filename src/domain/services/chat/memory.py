"""Compact memory for coach turns that no longer fit in the live history window."""

from __future__ import annotations

from collections.abc import Sequence

from src.domain.model.chat import CHAT_HISTORY_LIMIT, ChatMessage, ChatMessageRole

# Live history stays at CHAT_HISTORY_LIMIT. This is how many completed messages
# we read so older turns can be folded into a short memory block.
CHAT_MEMORY_FETCH = CHAT_HISTORY_LIMIT * 3
_LINE_CHARS = 160
_MEMORY_CHARS = 1400


def earlier_conversation(messages: Sequence[ChatMessage]) -> str | None:
    """Return older turns as untrusted memory. The newest window stays in history."""
    lines = [_memory_line(message) for message in messages]
    visible = [line for line in lines if line]
    if len(visible) <= CHAT_HISTORY_LIMIT:
        return None
    return _cap(visible[:-CHAT_HISTORY_LIMIT])


def _memory_line(message: ChatMessage) -> str | None:
    if message.role == ChatMessageRole.USER:
        prefix = "User"
    elif message.role == ChatMessageRole.ASSISTANT:
        prefix = "Coach"
    else:
        return None
    content = " ".join((message.content or "").split())
    if not content and message.role == ChatMessageRole.ASSISTANT:
        names = [
            str(card.get("name")).strip()
            for card in message.suggestions()
            if isinstance(card, dict) and str(card.get("name") or "").strip()
        ]
        if names:
            content = f"Showed a meal card for {names[0]}"
    if not content:
        return None
    return f"{prefix}: {content}"


def _cap(lines: Sequence[str]) -> str | None:
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        clipped = line
        if len(clipped) > _LINE_CHARS:
            clipped = clipped[: _LINE_CHARS - 1].rstrip() + "…"
        extra = len(clipped) + (1 if kept else 0)
        if total + extra > _MEMORY_CHARS:
            break
        kept.append(clipped)
        total += extra
    if not kept:
        return None
    return "\n".join(reversed(kept))
