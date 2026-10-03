from dataclasses import dataclass

from src.app.events.base import Command


@dataclass
class AiAdjustMealPlanCommand(Command):
    user_id: str
    plan_id: str
    prompt: str
    target_day_index: int | None = None
    target_slot_index: int | None = None

    language: str = "en"
