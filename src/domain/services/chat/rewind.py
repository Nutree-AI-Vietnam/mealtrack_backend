"""Which chat messages are dropped when an earlier reply is edited."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime


def message_ids_after(
    target_id: str,
    target_created_at: datetime,
    target_role: str,
    messages: Sequence[tuple[str, datetime, str]],
) -> list[str]:
    """Return ids strictly after the target.

    Equal timestamps put the user message before the assistant, matching
    chronological_chat_messages. A turn pair that shares created_at must not
    delete the question when its reply is reopened.
    """

    def sort_key(message_id: str, created_at: datetime, role: str) -> tuple:
        return (created_at, 0 if role == "user" else 1, message_id)

    target_key = sort_key(target_id, target_created_at, target_role)
    return [
        message_id
        for message_id, created_at, role in messages
        if message_id != target_id
        and sort_key(message_id, created_at, role) > target_key
    ]
