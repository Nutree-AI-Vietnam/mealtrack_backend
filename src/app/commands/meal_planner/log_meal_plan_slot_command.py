from dataclasses import dataclass
from datetime import date

from src.app.events.base import Command


@dataclass
class LogMealPlanSlotCommand(Command):
    user_id: str
    plan_id: str
    slot_id: str
    idempotency_key: str
    meal_date: date
    meal_type: str
    portion_multiplier: float
    timezone: str
