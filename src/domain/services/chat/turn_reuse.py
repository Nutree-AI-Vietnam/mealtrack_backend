"""Decide whether a chat retry generates again on the row it already has.

A job retry calls prepare again with the same turn. Inserting another user
or assistant row would ask the question a second time. The reply row is
reused instead: a finished reply is replayed, a live lease is left alone,
and a failed or expired reply is generated again in place.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from src.domain.model.chat.models import ChatMessageStatus

ReplyDisposition = Literal["replay", "busy", "reuse", "create"]


def existing_reply_disposition(
    *,
    status: str | None,
    lease_expires_at: datetime | None,
    now: datetime,
) -> ReplyDisposition:
    """How a retry should treat the assistant row stored for this turn."""
    if status == ChatMessageStatus.COMPLETED.value:
        return "replay"
    if status == ChatMessageStatus.GENERATING.value:
        if lease_expires_at is not None and lease_expires_at > now:
            return "busy"
        return "reuse"
    if status == ChatMessageStatus.FAILED.value:
        return "reuse"
    return "create"


def should_regenerate_on_resume(
    *, disposition: ReplyDisposition, is_latest: bool
) -> bool:
    """A delayed retry generates again only on the latest failed or expired reply.

    A completed reply is replayed by the caller. An older reply is left alone
    so the retry cannot overwrite a turn the user has already moved past.
    """
    return disposition == "reuse" and is_latest


def replacement_question(
    stored: str, incoming: str | None, *, limit: int
) -> str | None:
    """Return new question text only when it actually changes.

    ``None`` means the stored question stays, including its ``updated_at``.
    """
    edited = (incoming or "").strip()
    if not edited:
        return None
    # Resume sends the original question every time. Once it has been
    # stored at the length limit, that truncated text is not a new edit.
    truncated = edited[:limit]
    if truncated == stored.strip():
        return None
    return truncated


def reuse_assistant_row(
    row: Any,
    *,
    lease_expires_at: datetime,
    now: datetime,
    generation_id: str,
) -> None:
    """Put an existing assistant row back into generation without a new id."""
    row.status = ChatMessageStatus.GENERATING.value
    row.content = None
    row.reply_payload = None
    row.error_code = None
    row.completed_at = None
    row.generation_lease_expires_at = lease_expires_at
    row.generation_id = generation_id
    row.updated_at = now
