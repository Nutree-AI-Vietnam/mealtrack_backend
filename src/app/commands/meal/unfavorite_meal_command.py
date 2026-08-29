"""Command to unfavorite a meal."""

from dataclasses import dataclass

from src.app.events.base import Command


@dataclass
class UnfavoriteMealCommand(Command):
    user_id: str
    meal_id: str
