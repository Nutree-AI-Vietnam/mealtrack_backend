"""Supplement commands: the person's list and the doses they take."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.app.events.base import Command


@dataclass
class AddSupplementCommand(Command):
    """Put a visible product on the list; ``log_now`` also logs one dose."""

    user_id: str
    product_id: str
    units_per_dose: float | None = None
    log_now: bool = False
    intake_id: str | None = None
    taken_at: datetime | None = None
    header_timezone: str | None = None
    replay_key: Any = None


@dataclass
class UpdateSupplementCommand(Command):
    user_id: str
    entry_id: str
    units_per_dose: float | None = None
    active: bool | None = None
    sort_order: int | None = None


@dataclass
class RemoveSupplementCommand(Command):
    """Take a product off the list; past doses stay."""

    user_id: str
    entry_id: str


@dataclass
class LogSupplementIntakeCommand(Command):
    """Log one dose under the client's ``intake_id``.

    With ``replay_key`` (from the Idempotency-Key header) a retry replays the
    stored reply instead of answering 409.
    """

    user_id: str
    intake_id: str
    product_id: str
    units: float
    taken_at: datetime | None = None
    header_timezone: str | None = None
    replay_key: Any = None


@dataclass
class ScanSupplementLabelCommand(Command):
    """Read a label photo into the user's own product, list it and log a dose.

    A protein powder, gainer or meal shake is saved as a meal instead.
    """

    user_id: str
    image_url: str
    intake_id: str
    taken_at: datetime | None = None
    header_timezone: str | None = None
    replay_key: Any = None


@dataclass
class DeleteSupplementIntakeCommand(Command):
    user_id: str
    intake_id: str


__all__ = [
    "AddSupplementCommand",
    "DeleteSupplementIntakeCommand",
    "LogSupplementIntakeCommand",
    "RemoveSupplementCommand",
    "ScanSupplementLabelCommand",
    "UpdateSupplementCommand",
]
