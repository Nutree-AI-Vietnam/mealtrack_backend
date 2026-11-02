"""The + panel's Ghi nhanh: one sentence logs food, drinks, activities and supplements."""

from dataclasses import dataclass
from datetime import date, time

from src.app.events.base import Command


@dataclass
class DescribeAnyCommand(Command):
    user_id: str
    text: str  # already sanitised by the describe guard
    kinds: frozenset[str]
    target_date: date | None = None
    local_time: time | None = None  # the phone's clock, for the meal type
    header_timezone: str | None = None
    language: str = "vi"
    # The request_id claim; None skips it (internal callers and tests).
    idempotency_key: str | None = None
    request_fingerprint: str | None = None


@dataclass
class UndoDescribeAnyCommand(Command):
    """Delete everything one sentence saved, in one transaction."""

    user_id: str
    request_id: str


__all__ = ["DescribeAnyCommand", "UndoDescribeAnyCommand"]
