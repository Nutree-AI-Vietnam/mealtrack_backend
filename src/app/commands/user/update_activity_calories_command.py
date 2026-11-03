"""Command to update the activity calories preference."""

from dataclasses import dataclass

from src.app.events.base import Command


@dataclass
class UpdateActivityCaloriesCommand(Command):
    """Let or stop calories burned in logged movement raising the food budget."""

    user_id: str
    enabled: bool
