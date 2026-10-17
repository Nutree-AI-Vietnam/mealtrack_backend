"""Derive thread-list labels from stored message text.

Titles come from the thread's first question and previews from its latest
message. Nothing is generated, so a label never says more than the user or
coach already did.
"""

from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime

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


def encode_thread_cursor(updated_at: datetime, thread_id: str) -> str:
    """Opaque list cursor that keeps its place if that thread changes later."""
    raw = f"{updated_at.isoformat()}|{thread_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_thread_cursor(cursor: str) -> tuple[datetime, str] | None:
    """Return ``(updated_at, thread_id)``, or None for a malformed cursor."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        stamp, _, thread_id = (
            base64.urlsafe_b64decode(padded.encode()).decode().partition("|")
        )
        updated_at = datetime.fromisoformat(stamp)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if not thread_id or updated_at.tzinfo is None:
        return None
    return updated_at, thread_id
