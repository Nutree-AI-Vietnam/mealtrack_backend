"""Command to log every drink in one sentence (Ghi nhanh)."""

from dataclasses import dataclass
from datetime import date

from src.app.events.base import Command


@dataclass
class DescribeHydrationCommand(Command):
    user_id: str
    text: str  # already sanitised by the describe guard
    target_date: date | None = None
    header_timezone: str | None = None
    language: str = "en"
    # Idempotency-Key claim; None skips it (internal callers and tests).
    idempotency_key: str | None = None
    request_fingerprint: str | None = None
