"""Derive thread-list labels from stored message text.

Titles come from the thread's first question and previews from its latest
message. Nothing is generated, so a label never says more than the user or
coach already did.
"""

from __future__ import annotations

import re

from src.domain.model.chat import (
    CHAT_THREAD_PREVIEW_MAX_CHARS,
    CHAT_THREAD_TITLE_MAX_CHARS,
)

# Coach replies are markdown; a one-line preview should show the words only.
_MARKDOWN_MARKS = re.compile(r"[*_`#>]+|\[([^\]]*)\]\([^)]*\)")


def thread_title(first_user_message: str | None) -> str | None:
    return _clip(first_user_message, CHAT_THREAD_TITLE_MAX_CHARS)


def thread_preview(latest_message: str | None) -> str | None:
    text = _MARKDOWN_MARKS.sub(lambda m: m.group(1) or "", latest_message or "")
    return _clip(text, CHAT_THREAD_PREVIEW_MAX_CHARS)


def _clip(text: str | None, limit: int) -> str | None:
    collapsed = " ".join((text or "").split())
    if not collapsed:
        return None
    if len(collapsed) <= limit:
        return collapsed
    cut = collapsed[: limit - 1].rstrip()
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return f"{cut}…"
