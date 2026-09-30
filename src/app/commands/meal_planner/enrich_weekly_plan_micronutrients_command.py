from dataclasses import dataclass

from src.app.events.base import Command


@dataclass
class EnrichWeeklyPlanMicronutrientsCommand(Command):
    """Persist cached micronutrients for recipes in a saved weekly plan."""

    recipe_ids: tuple[str, ...]
