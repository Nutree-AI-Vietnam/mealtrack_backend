"""Compact memory for coach turns that no longer fit in the live history window."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.domain.model.chat import CHAT_HISTORY_LIMIT, ChatMessage, ChatMessageRole

# Live history stays at CHAT_HISTORY_LIMIT. This is how many completed messages
# we read so older turns can be folded into a short memory block.
CHAT_MEMORY_FETCH = CHAT_HISTORY_LIMIT * 3
_LINE_CHARS = 160
_MEMORY_CHARS = 1400


@dataclass(frozen=True, slots=True)
class CoachMemory:
    """Prompt text, plus the message id to store when the summary should advance."""

    text: str | None = None
    through_message_id: str | None = None


def earlier_conversation(messages: Sequence[ChatMessage]) -> str | None:
    """Return older turns as untrusted memory. The newest window stays in history."""
    return rolling_memory(messages).text


def rolling_memory(
    messages: Sequence[ChatMessage],
    *,
    stored_summary: str | None = None,
    summary_through_message_id: str | None = None,
) -> CoachMemory:
    """Merge the saved summary with turns that just fell out of the live window."""
    visible = [
        (message.id, line)
        for message in messages
        if (line := _memory_line(message)) is not None
    ]
    stored = (stored_summary or "").strip() or None
    if len(visible) <= CHAT_HISTORY_LIMIT:
        return CoachMemory(text=stored)

    overflow = visible[:-CHAT_HISTORY_LIMIT]
    new_lines = _lines_after(overflow, summary_through_message_id)
    merged: list[str] = []
    if stored:
        merged.extend(line for line in stored.splitlines() if line.strip())
    merged.extend(new_lines)
    text = _cap(merged) if merged else None
    through = overflow[-1][0] if new_lines else None
    return CoachMemory(text=text, through_message_id=through)


def _lines_after(
    overflow: Sequence[tuple[str, str]],
    summary_through_message_id: str | None,
) -> list[str]:
    if not summary_through_message_id:
        return [line for _, line in overflow]
    for index, (message_id, _) in enumerate(overflow):
        if message_id == summary_through_message_id:
            return [line for _, line in overflow[index + 1 :]]
    # The marker is older than this page, so those lines are already stored.
    return []


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
