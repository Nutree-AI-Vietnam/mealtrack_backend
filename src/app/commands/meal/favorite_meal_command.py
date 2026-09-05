"""Command to favorite a meal."""

from dataclasses import dataclass

from src.app.events.base import Command


@dataclass
class FavoriteMealCommand(Command):
    user_id: str
    meal_id: str
