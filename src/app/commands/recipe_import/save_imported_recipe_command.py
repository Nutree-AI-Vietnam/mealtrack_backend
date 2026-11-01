"""Save a reviewed recipe draft to the user's saved recipes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.app.events.base import Command


@dataclass
class SaveImportedRecipeCommand(Command):
    user_id: str
    import_id: str
    language: str
    # The draft as the app shows it after the user's edits; the backend
    # re-prices every ingredient, so client nutrition is never trusted.
    recipe: dict[str, Any] = field(default_factory=dict)
    meal_type: str = "dinner"
