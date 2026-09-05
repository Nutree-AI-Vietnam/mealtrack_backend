"""Command to update leftover-split daily adjustment preference."""

from dataclasses import dataclass
from uuid import UUID

from src.app.events.base import Command


@dataclass
class UpdateWeeklyAutoAdjustCommand(Command):
    """Enable or disable leftover-split daily targets."""

    user_id: UUID
    enabled: bool
