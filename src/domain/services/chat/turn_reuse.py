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
