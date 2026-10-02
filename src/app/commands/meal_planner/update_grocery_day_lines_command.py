from dataclasses import dataclass

from src.app.events.base import Command


@dataclass
class UpdateGroceryDayLinesCommand(Command):
    user_id: str
    plan_id: str
    idempotency_key: str
    ingredient_id: int
    lines: list[dict]
