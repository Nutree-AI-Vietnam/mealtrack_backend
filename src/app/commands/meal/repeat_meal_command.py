"""Command to repeat a meal idempotently."""

from dataclasses import dataclass

from src.app.events.base import Command


@dataclass
class RepeatMealCommand(Command):
    user_id: str
    meal_id: str
    idempotency_key: str
    meal_type: str | None = None
    language: str = "en"
