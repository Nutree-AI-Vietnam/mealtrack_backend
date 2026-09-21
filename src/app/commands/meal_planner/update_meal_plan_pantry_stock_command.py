from dataclasses import dataclass

from src.app.events.base import Command


@dataclass
class UpdateMealPlanPantryStockCommand(Command):
    user_id: str
    plan_id: str
    idempotency_key: str
    updates: list[dict]
