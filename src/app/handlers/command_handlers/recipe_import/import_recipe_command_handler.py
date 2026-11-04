"""Handler: read a recipe source into a priced, unsaved draft."""

from __future__ import annotations

from src.app.commands.recipe_import import ImportRecipeCommand
from src.app.events.base import EventHandler, handles
from src.app.services.recipe_import.recipe_import_service import (
    RecipeImportRequest,
    RecipeImportService,
)
from src.domain.model.recipe_import.imported_recipe import ImportedRecipe


@handles(ImportRecipeCommand)
class ImportRecipeCommandHandler(EventHandler[ImportRecipeCommand, ImportedRecipe]):
    def __init__(self, service: RecipeImportService) -> None:
        self.service = service

    async def handle(self, command: ImportRecipeCommand) -> ImportedRecipe:
        return await self.service.import_recipe(
            RecipeImportRequest(
                language=command.language,
                url=command.url,
                text=command.text,
                image_url=command.image_url,
                note=command.note,
            )
        )
