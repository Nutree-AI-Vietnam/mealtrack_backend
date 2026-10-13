"""Structured follow-up chips for completed chat turns."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from src.domain.model.chat import CHAT_INTENTS

_MAX_FOLLOW_UPS = 3
_MAX_LABEL_CHARS = 48
_MIN_ASK_CHARS = 6
# A chip whose label is the next user message. Mobile sends it as free text.
ASK_ACTION = "ask"
_VAGUE_LABELS = frozenset(
    {
        "tell me more",
        "more",
        "ok",
        "okay",
        "yes",
        "no",
        "sure",
        "nói thêm",
        "thêm",
        "được",
        "oke",
        "okê",
    }
)
_NUMBERED_CLAIM_RE = re.compile(
    r"\d[\d.,]*\s*(?:kcal|calories?|cal|g(?:rams?)?)\b",
    re.IGNORECASE,
)
_MUTATION_CLAIM_RE = re.compile(
    r"\b(i\s+(?:logged|saved|updated)|logged\s+it|saved\s+it|đã\s+ghi|đã\s+lưu)\b",
    re.IGNORECASE,
)


class ChatFollowUpItem(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    action: str = Field(min_length=1, max_length=32)


class ChatFollowUpList(BaseModel):
    follow_ups: list[ChatFollowUpItem] = Field(default_factory=list, max_length=4)


def meal_names(suggestions: Sequence[Mapping[str, Any]]) -> list[str]:
    """Dish names a follow-up chip can refer to, capped at three."""
    names: list[str] = []
    for item in suggestions:
        name = " ".join(str(item.get("name") or "").split())
        if not name:
            continue
        names.append(name[:80])
        if len(names) >= 3:
            break
    return names


def sanitize_follow_ups(raw: Any) -> list[dict[str, str]]:
    """Keep up to 3 chips.

    A known intent alias opens that Nutree card. ``ask`` sends the label as
    the next user message. Other actions are dropped.
    """
    from src.domain.services.chat.coach_functions import (
        preferred_alias,
        resolve_coach_function,
    )

    items = _coerce_items(raw)
    cleaned: list[dict[str, str]] = []
    seen_actions: set[str] = set()
    seen_labels: set[str] = set()
    for item in items:
        label = " ".join(str(item.get("label") or "").split())
        action = str(item.get("action") or "").strip()
        if not label or not action:
            continue
        if action == ASK_ACTION:
            ask_label = _ask_label(label)
            if ask_label is None or ask_label.casefold() in seen_labels:
                continue
            seen_labels.add(ask_label.casefold())
            cleaned.append({"label": ask_label, "action": ASK_ACTION})
        else:
            alias = _intent_alias(action, preferred_alias, resolve_coach_function)
            if alias is None or alias in seen_actions:
                continue
            seen_actions.add(alias)
            cleaned.append({"label": label[:_MAX_LABEL_CHARS], "action": alias})
        if len(cleaned) >= _MAX_FOLLOW_UPS:
            break
    return cleaned


def _intent_alias(
    action: str, preferred_alias: Any, resolve_coach_function: Any
) -> str | None:
    if action in CHAT_INTENTS:
        return action
    resolved = resolve_coach_function(action)
    if resolved is None:
        return None
    alias = preferred_alias(resolved.name, resolved.args)
    if alias is None or alias not in CHAT_INTENTS:
        return None
    return alias


def _ask_label(label: str) -> str | None:
    trimmed = label[:_MAX_LABEL_CHARS].strip()
    if len(trimmed) < _MIN_ASK_CHARS:
        return None
    if trimmed.casefold() in _VAGUE_LABELS:
        return None
    if _NUMBERED_CLAIM_RE.search(trimmed) or _MUTATION_CLAIM_RE.search(trimmed):
        return None
    return trimmed


def ensure_follow_ups(
    raw: Any,
    *,
    locale: str,
    intent: str | None,
    slot: str | None,
    has_suggestions: bool,
    suggestion_names: Sequence[str] = (),
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
        suggestion_names=suggestion_names,
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
    suggestion_names: Sequence[str] = (),
) -> list[dict[str, str]]:
    vi = locale == "vi"
    chips: list[dict[str, str]] = []
    if has_suggestions:
        named = _higher_protein_chip(suggestion_names, vi=vi)
        if named is not None:
            chips.append(named)
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
    chips.extend(
        {"label": _fallback_label(action, slot=slot, vi=vi), "action": action}
        for action in ordered
    )
    return chips


def _higher_protein_chip(names: Sequence[str], *, vi: bool) -> dict[str, str] | None:
    """A tap that changes the featured dish, not another generic meal search."""
    featured = ""
    for name in names:
        cleaned = " ".join(str(name).split())
        if cleaned:
            featured = cleaned
            break
    if not featured:
        return None
    label = f"Làm {featured} nhiều đạm hơn" if vi else f"Make {featured} higher protein"
    if not _MIN_ASK_CHARS <= len(label) <= _MAX_LABEL_CHARS:
        return None
    if _NUMBERED_CLAIM_RE.search(label) or _MUTATION_CLAIM_RE.search(label):
        return None
    return {"label": label, "action": ASK_ACTION}


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
