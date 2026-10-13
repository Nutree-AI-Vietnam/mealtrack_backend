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
    """Keep 2–3 chips whose action is a known alias (or registry function name).

    Function names are mapped to their preferred chip alias before emit so
    mobile `CoachIntent` parsing stays stable.
    """
    from src.domain.services.chat.coach_functions import (
        preferred_alias,
        resolve_coach_function,
    )

    items = _coerce_items(raw)
    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        label = " ".join(str(item.get("label") or "").split())
        action = str(item.get("action") or "").strip()
        if not label or not action:
            continue
        if action in CHAT_INTENTS:
            alias = action
        else:
            resolved = resolve_coach_function(action)
            if resolved is None:
                continue
            alias = preferred_alias(resolved.name, resolved.args)
            if alias is None or alias not in CHAT_INTENTS:
                continue
        if alias in seen:
            continue
        seen.add(alias)
        cleaned.append({"label": label[:_MAX_LABEL_CHARS], "action": alias})
        if len(cleaned) >= _MAX_FOLLOW_UPS:
            break
    return cleaned


def ensure_follow_ups(
    raw: Any,
    *,
    locale: str,
    intent: str | None,
    slot: str | None,
    has_suggestions: bool,
) -> list[dict[str, str]]:
    """Keep model chips, and fill at least two when that call is short or fails."""
    chips = sanitize_follow_ups(raw)
    if len(chips) >= 2:
        return chips
    seen = {chip["action"] for chip in chips}
    for chip in _fallback_chips(
        locale=locale,
        intent=intent,
        slot=slot,
        has_suggestions=has_suggestions,
    ):
        if chip["action"] in seen:
            continue
        chips.append(chip)
        seen.add(chip["action"])
        if len(chips) >= 2:
            break
    return chips


def _fallback_chips(
    *,
    locale: str,
    intent: str | None,
    slot: str | None,
    has_suggestions: bool,
) -> list[dict[str, str]]:
    vi = locale == "vi"
    ordered: list[str] = []
    if has_suggestions or intent == "next_meal":
        ordered.append("next_meal")
    if intent != "remaining_budget":
        ordered.append("remaining_budget")
    if intent != "day_progress":
        ordered.append("day_progress")
    if "next_meal" not in ordered:
        ordered.append("next_meal")
    if intent != "limits":
        ordered.append("limits")
    return [
        {"label": _fallback_label(action, slot=slot, vi=vi), "action": action}
        for action in ordered
    ]


def _fallback_label(action: str, *, slot: str | None, vi: bool) -> str:
    if action == "next_meal":
        labels = {
            "breakfast": ("Thêm ý tưởng bữa sáng", "More breakfast ideas"),
            "lunch": ("Thêm ý tưởng bữa trưa", "More lunch ideas"),
            "dinner": ("Thêm ý tưởng bữa tối", "More dinner ideas"),
            "snack": ("Thêm ý tưởng món nhẹ", "More snack ideas"),
        }
        vi_label, en_label = labels.get(
            slot or "",
            ("Gợi ý bữa khác", "Another meal idea"),
        )
        return vi_label if vi else en_label
    labels = {
        "remaining_budget": ("Hôm nay còn bao nhiêu?", "What's left today?"),
        "day_progress": ("Hôm nay tôi thế nào?", "How's my day going?"),
        "limits": ("Coach làm được gì?", "What can Coach do?"),
    }
    vi_label, en_label = labels[action]
    return vi_label if vi else en_label


def _coerce_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, ChatFollowUpList):
        return [item.model_dump() for item in raw.follow_ups]
    if isinstance(raw, dict):
        raw = raw.get("follow_ups")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]
