"""Strip secret query parameters from text that can reach logs or Sentry.

httpx's ``raise_for_status`` copies the full request URL into the exception
message. USDA calls put ``api_key`` on that URL, and the event-bus error log
used the message as the Sentry issue title.
"""

from __future__ import annotations

import re
from typing import Any

# Query form: ``?api_key=value`` or ``&api_key=value``.
_QUERY_SECRET = re.compile(
    r"(?i)([?&])(api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|passwd|secret|token|key)=([^&#\s'\"<>]*)"
)
# Same names when a log line drops the leading ``?`` or ``&``.
_NAMED_SECRET = re.compile(
    r"(?i)(?<![?&])\b(api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret)=([^&#\s'\"<>]*)"
)
_ENCODED_SECRET = re.compile(
    r"(?i)(api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret)(%3[Dd])([^%&#\s'\"<>]*)"
)
_FILTERED = "[Filtered]"


def redact_sensitive_text(text: str) -> str:
    """Replace secret query values with ``[Filtered]``."""

    def _query(match: re.Match[str]) -> str:
        return f"{match.group(1)}{match.group(2)}={_FILTERED}"

    def _named(match: re.Match[str]) -> str:
        return f"{match.group(1)}={_FILTERED}"

    def _encoded(match: re.Match[str]) -> str:
        return f"{match.group(1)}{match.group(2)}{_FILTERED}"

    redacted = _QUERY_SECRET.sub(_query, text)
    redacted = _NAMED_SECRET.sub(_named, redacted)
    return _ENCODED_SECRET.sub(_encoded, redacted)


def scrub_exception_text(exc: BaseException) -> BaseException:
    """Redact secret query values on ``exc`` and its visible cause chain."""
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending:
        current = pending.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        _scrub_one(current)
        cause = current.__cause__
        context = current.__context__
        if isinstance(cause, BaseException):
            pending.append(cause)
        if isinstance(context, BaseException):
            pending.append(context)
        nested = getattr(current, "exceptions", None)
        if isinstance(nested, tuple):
            pending.extend(item for item in nested if isinstance(item, BaseException))
    return exc


def redact_sentry_event(event: dict[str, Any]) -> dict[str, Any]:
    """Redact secret query values anywhere in a Sentry event payload."""
    _redact_in_place(event)
    return event


def _scrub_one(exc: BaseException) -> None:
    if exc.args:
        exc.args = tuple(
            redact_sensitive_text(arg) if isinstance(arg, str) else arg
            for arg in exc.args
        )
    attributes = getattr(exc, "__dict__", None)
    if isinstance(attributes, dict):
        message = attributes.get("message")
        if isinstance(message, str):
            attributes["message"] = redact_sensitive_text(message)
    _scrub_http_request(exc)


def _scrub_http_request(exc: BaseException) -> None:
    request = getattr(exc, "request", None)
    url = getattr(request, "url", None)
    copy_with = getattr(url, "copy_with", None)
    if request is None or not callable(copy_with):
        return
    if not getattr(url, "query", b""):
        return
    try:
        clean_url = copy_with(query=None)
        clean_request = type(request)(getattr(request, "method", "GET"), clean_url)
    except (TypeError, ValueError):
        return
    try:
        exc.request = clean_request
    except (AttributeError, TypeError):
        return
    response = getattr(exc, "response", None)
    if response is None:
        return
    try:
        if getattr(response, "request", None) is request:
            response.request = clean_request
    except (AttributeError, TypeError):
        return


def _redact_in_place(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if isinstance(item, str):
                value[key] = redact_sensitive_text(item)
            else:
                _redact_in_place(item)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str):
                value[index] = redact_sensitive_text(item)
            else:
                _redact_in_place(item)
        return
    if isinstance(value, tuple):
        for item in value:
            if not isinstance(item, str):
                _redact_in_place(item)
