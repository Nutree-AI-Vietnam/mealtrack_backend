"""Timezone loaded with the authenticated user for the current request.

Auth already reads the active users row. The timezone on that row is bound
here so later handlers do not issue a second ``SELECT users.timezone``. The
value is the row just read, not a separate cache with its own expiry. A
committed timezone write updates the bound value and any auth snapshot that
still names this user.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar

_request_user_timezone: ContextVar[tuple[str, str] | None] = ContextVar(
    "mealtrack_request_user_timezone",
    default=None,
)

_changed_listeners: list[Callable[[str, str], None]] = []


def bind_request_user_timezone(user_id: str, timezone: str) -> None:
    """Remember the timezone loaded for this user on the current task."""
    _request_user_timezone.set((str(user_id), timezone))


def clear_request_user_timezone() -> None:
    """Drop the request binding so a later task cannot reuse it."""
    _request_user_timezone.set(None)


def peek_request_user_timezone(user_id: str) -> str | None:
    """Return the bound timezone when it belongs to ``user_id``."""
    current = _request_user_timezone.get()
    if current is None:
        return None
    bound_user_id, timezone = current
    if bound_user_id != str(user_id):
        return None
    return timezone


def register_user_timezone_changed(listener: Callable[[str, str], None]) -> None:
    """Register a process-local follower of a committed timezone write."""
    if listener not in _changed_listeners:
        _changed_listeners.append(listener)


def publish_user_timezone_changed(user_id: str, timezone: str) -> None:
    """Apply a committed timezone to this process.

    Updates the request binding when it names the same user, then notifies
    followers (the auth identity snapshot). Call only after the write has
    committed, or when the database already stores ``timezone``.
    """
    user_id_text = str(user_id)
    if peek_request_user_timezone(user_id_text) is not None:
        bind_request_user_timezone(user_id_text, timezone)
    for listener in tuple(_changed_listeners):
        listener(user_id_text, timezone)
