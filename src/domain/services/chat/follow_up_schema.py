"""Structured follow-up chips for completed chat turns."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.domain.model.chat import CHAT_INTENTS

_MAX_FOLLOW_UPS = 3
_MAX_LABEL_CHARS = 48


class ChatFollowUpItem(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    action: str = Field(min_length=1, max_length=32)


class ChatFollowUpList(BaseModel):
    follow_ups: list[ChatFollowUpItem] = Field(default_factory=list, max_length=4)


def sanitize_follow_ups(raw: Any) -> list[dict[str, str]]:
    """Keep 2–3 chips whose action is a known ChatIntent. Drop the rest."""
    items = _coerce_items(raw)
    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        label = " ".join(str(item.get("label") or "").split())
        action = str(item.get("action") or "").strip()
        if not label or action not in CHAT_INTENTS:
            continue
        if action in seen:
            continue
        seen.add(action)
        cleaned.append({"label": label[:_MAX_LABEL_CHARS], "action": action})
        if len(cleaned) >= _MAX_FOLLOW_UPS:
            break
    return cleaned


def _coerce_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, ChatFollowUpList):
        return [item.model_dump() for item in raw.follow_ups]
    if isinstance(raw, dict):
        raw = raw.get("follow_ups")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]
