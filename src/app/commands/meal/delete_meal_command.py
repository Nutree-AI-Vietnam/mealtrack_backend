"""
Command to mark a meal as INACTIVE (soft delete).
"""

from dataclasses import dataclass

from src.app.events.base import Command


@dataclass
class DeleteMealCommand(Command):
    meal_id: str
    user_id: str  # Required for ownership verification
