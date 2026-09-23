from dataclasses import dataclass

from src.app.events.base import Command


@dataclass
class AiAdjustMealPlanCommand(Command):
    user_id: str
    plan_id: str
    prompt: str
