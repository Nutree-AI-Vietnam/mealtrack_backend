"""Command to log every activity in one sentence (Ghi nhanh)."""

from dataclasses import dataclass
from datetime import date

from src.app.events.base import Command


@dataclass
class DescribeMovementCommand(Command):
    user_id: str
    text: str  # already sanitised by the describe guard
    target_date: date | None = None
    weight_kg: float | None = None  # the weight the app prices activities with
    header_timezone: str | None = None
    # Idempotency-Key claim; None skips it (internal callers and tests).
    idempotency_key: str | None = None
    request_fingerprint: str | None = None
