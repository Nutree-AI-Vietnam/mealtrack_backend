"""Read a recipe from one source into a reviewable draft."""

from __future__ import annotations

from dataclasses import dataclass

from src.app.events.base import Command
from src.domain.exceptions.recipe_import_exceptions import (
    RecipeImportError,
    RecipeImportFailure,
)


@dataclass
class ImportRecipeCommand(Command):
    user_id: str
    language: str
    url: str | None = None
    text: str | None = None
    image_url: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        sources = [value for value in (self.url, self.text, self.image_url) if value]
        if len(sources) != 1:
            raise RecipeImportError(
                RecipeImportFailure.INVALID_SOURCE,
                "Send exactly one of url, text or image_url.",
            )
