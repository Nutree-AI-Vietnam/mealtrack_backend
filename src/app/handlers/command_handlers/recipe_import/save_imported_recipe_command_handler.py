"""Handler: re-price a reviewed draft and save it as a saved recipe.

Saved recipes reuse saved suggestions, so they appear in the app's saved
list and open in the existing recipe screen. The snapshot is rebuilt from
the edited ingredients with backend nutrition; client macros are ignored.
Ingredient amounts are stored as reviewed, for the whole recipe; the
servings only set how the totals are divided.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from src.app.commands.recipe_import import SaveImportedRecipeCommand
from src.app.commands.saved_suggestion import SaveSuggestionCommand
from src.app.events.base import EventHandler, handles
from src.app.services.recipe_import.imported_recipe_builder import (
    saved_suggestion_id,
    saved_suggestion_payload,
)
from src.app.services.recipe_import.recipe_nutrition_pricer import (
    RecipeNutritionPricer,
)
from src.domain.exceptions.recipe_import_exceptions import (
    RecipeImportError,
    RecipeImportFailure,
)
from src.domain.model.common.enums import MealType
from src.domain.model.recipe_import.enums import (
    RecipeImportSourceType,
    RecipeImportWarning,
    RecipeSourcePlatform,
)
from src.domain.model.recipe_import.imported_recipe import (
    ImportedRecipe,
    RecipeSourceInfo,
)
from src.domain.model.recipe_import.limits import (
    MAX_DESCRIPTION_CHARS,
    MAX_EMOJI_CHARS,
    MAX_LABEL_CHARS,
    MAX_MINUTES,
    MAX_NAME_CHARS,
    MAX_RECIPE_URL_LENGTH,
)
from src.domain.model.recipe_import.sanitizer import (
    sanitize_ingredients,
    sanitize_steps,
)
from src.domain.model.recipe_import.text import clamp_servings, clean_text
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.recipe_import_ports import RecipeImageArchivePort

Send = Callable[[Any], Awaitable[Any]]
_MEAL_TYPES = {meal_type.value: meal_type for meal_type in MealType}


@handles(SaveImportedRecipeCommand)
class SaveImportedRecipeCommandHandler(
    EventHandler[SaveImportedRecipeCommand, dict[str, Any]]
):
    def __init__(
        self,
        *,
        pricer: RecipeNutritionPricer,
        send: Send,
        uow_factory: Callable[[], AsyncUnitOfWorkPort],
        image_archive: RecipeImageArchivePort | None = None,
    ) -> None:
        self.pricer = pricer
        self.send = send
        self.uow_factory = uow_factory
        self.image_archive = image_archive

    async def handle(self, command: SaveImportedRecipeCommand) -> dict[str, Any]:
        suggestion_id = saved_suggestion_id(command.import_id)
        async with self.uow_factory() as uow:
            existing = await uow.saved_suggestions.find_by_user_and_suggestion(
                command.user_id, suggestion_id
            )
        if existing:
            return existing
        recipe = await self._reviewed_recipe(command)
        return await self.send(
            SaveSuggestionCommand(
                user_id=command.user_id,
                suggestion_id=suggestion_id,
                meal_type=command.meal_type,
                portion_multiplier=1,
                suggestion_data=saved_suggestion_payload(recipe),
            )
        )

    async def _reviewed_recipe(
        self, command: SaveImportedRecipeCommand
    ) -> ImportedRecipe:
        raw = command.recipe
        name = clean_text(raw.get("name"), max_chars=MAX_NAME_CHARS)
        ingredients = sanitize_ingredients(raw.get("ingredients"))
        if not name or not ingredients:
            raise RecipeImportError(
                RecipeImportFailure.INVALID_SOURCE,
                "A recipe needs a name and at least one ingredient.",
            )
        priced = await self.pricer.price(
            ingredients, servings=_servings(raw.get("servings"))
        )
        warnings = []
        if priced.nutrition.unpriced_ingredients:
            warnings.append(RecipeImportWarning.NUTRITION_PARTIAL)
        if priced.nutrition.estimated:
            warnings.append(RecipeImportWarning.NUTRITION_ESTIMATED)
        return ImportedRecipe(
            import_id=command.import_id,
            language=command.language,
            name=name,
            servings=priced.nutrition.servings,
            ingredients=priced.ingredients,
            steps=sanitize_steps(raw.get("steps")),
            nutrition=priced.nutrition,
            source=_source(raw.get("source")),
            english_name=clean_text(raw.get("english_name"), max_chars=MAX_NAME_CHARS),
            description=clean_text(
                raw.get("description"), max_chars=MAX_DESCRIPTION_CHARS
            ),
            emoji=clean_text(raw.get("emoji"), max_chars=MAX_EMOJI_CHARS),
            cuisine_type=clean_text(raw.get("cuisine_type"), max_chars=MAX_LABEL_CHARS),
            origin_country=clean_text(
                raw.get("origin_country"), max_chars=MAX_LABEL_CHARS
            ),
            meal_type=_MEAL_TYPES.get(str(raw.get("meal_type") or "").lower()),
            prep_time_minutes=_minutes(raw.get("prep_time_minutes")),
            cook_time_minutes=_minutes(raw.get("cook_time_minutes")),
            total_time_minutes=_minutes(raw.get("total_time_minutes")),
            image_url=await self._image(raw.get("image_url")),
            warnings=tuple(warnings),
            servings_estimated=bool(raw.get("servings_estimated")),
            serving_description=clean_text(
                raw.get("serving_description"), max_chars=MAX_LABEL_CHARS
            ),
            makes=clean_text(raw.get("makes"), max_chars=MAX_LABEL_CHARS),
            original_servings=_servings(raw.get("original_servings")),
        )

    async def _image(self, value: Any) -> str | None:
        url = _http_url(value)
        if url is None or self.image_archive is None:
            return url
        return await self.image_archive.archive(url) or url


def _source(raw: Any) -> RecipeSourceInfo:
    data: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    try:
        source_type = RecipeImportSourceType(str(data.get("type") or "url"))
    except ValueError:
        source_type = RecipeImportSourceType.URL
    try:
        platform = RecipeSourcePlatform(str(data.get("platform") or "website"))
    except ValueError:
        platform = RecipeSourcePlatform.WEBSITE
    return RecipeSourceInfo(
        type=source_type,
        platform=platform,
        url=_http_url(data.get("url")),
        title=clean_text(data.get("title"), max_chars=MAX_NAME_CHARS),
        author=clean_text(data.get("author"), max_chars=MAX_LABEL_CHARS),
        site_name=clean_text(data.get("site_name"), max_chars=MAX_LABEL_CHARS),
        linked_recipe_url=_http_url(data.get("linked_recipe_url")),
    )


def _servings(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return clamp_servings(int(value))


def _minutes(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    minutes = int(value)
    return minutes if 0 < minutes <= MAX_MINUTES else None


def _http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    if len(url) > MAX_RECIPE_URL_LENGTH or not url.lower().startswith(
        ("https://", "http://")
    ):
        return None
    return url
