"""Recipe import: read a recipe from a link, pasted text or a photo.

``POST /extract`` returns an unsaved, backend-priced draft. ``POST /save``
re-prices the reviewed draft and stores it with the saved recipes. Long
video imports can also run in the background as a ``recipe_import`` job
(``POST /v1/ai-jobs``).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.dependencies.recipe_import import (
    get_recipe_import_guard,
    recipe_import_capabilities,
)
from src.api.exceptions import ExternalServiceException
from src.api.middleware.accept_language import get_request_language
from src.api.middleware.rate_limit import limiter
from src.api.routes.v1.meals_route_helpers import validate_uploaded_image_url
from src.api.schemas.request.recipe_import_requests import (
    RecipeImportSourceRequest,
    SaveImportedRecipeRequest,
)
from src.api.schemas.response.recipe_import_responses import (
    ImportedRecipeResponse,
    RecipeImportCapabilitiesResponse,
)
from src.app.commands.recipe_import import (
    ImportRecipeCommand,
    SaveImportedRecipeCommand,
)
from src.app.services.recipe_import import RecipeImportGuard
from src.domain.model.recipe_import.limits import IMPORT_DEADLINE_SECONDS

router = APIRouter(prefix="/v1/recipe-imports", tags=["Recipe Import"])

RECIPE_IMPORT_RATE_LIMIT = "10/minute"


@router.get("/capabilities", response_model=RecipeImportCapabilitiesResponse)
async def get_recipe_import_capabilities(
    user_id: str = Depends(get_current_user_id),
) -> RecipeImportCapabilitiesResponse:
    """Which sources this server can import from right now."""
    return RecipeImportCapabilitiesResponse(**recipe_import_capabilities())


@router.post("/extract", response_model=ImportedRecipeResponse)
@limiter.limit(RECIPE_IMPORT_RATE_LIMIT)
async def extract_recipe(
    request: Request,
    payload: RecipeImportSourceRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
    guard: RecipeImportGuard = Depends(get_recipe_import_guard),
) -> ImportedRecipeResponse:
    """Read one source into a draft. Nothing is saved.

    Videos whose caption is not enough are downloaded for speech and
    on-screen text, which can take a minute; the app should prefer the
    ``recipe_import`` background job for video links.
    """
    command = import_recipe_command(
        payload, user_id=user_id, language=get_request_language(request)
    )
    await guard.admit(user_id)
    try:
        async with asyncio.timeout(IMPORT_DEADLINE_SECONDS):
            recipe = await event_bus.send(command)
    except TimeoutError as exc:
        raise ExternalServiceException(
            "Importing this recipe took too long. Please try again.",
            "RECIPE_IMPORT_TIMEOUT",
        ) from exc
    return ImportedRecipeResponse.from_domain(recipe)


@router.post("/save")
@limiter.limit("30/minute")
async def save_imported_recipe(
    request: Request,
    payload: SaveImportedRecipeRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
) -> dict[str, Any]:
    """Save the reviewed draft; returns the saved item as in GET /v1/saved-suggestions.

    Saving the same ``import_id`` again returns the first save.
    """
    command = SaveImportedRecipeCommand(
        user_id=user_id,
        import_id=payload.import_id,
        language=get_request_language(request),
        recipe=payload.model_dump(mode="json", exclude={"import_id"}),
        meal_type=payload.meal_type,
    )
    result: dict[str, Any] = await event_bus.send(command)
    return result


def import_recipe_command(
    payload: RecipeImportSourceRequest, *, user_id: str, language: str
) -> ImportRecipeCommand:
    """Validate the source like other upload routes and build the command."""
    image_url = payload.image_url.strip() if payload.image_url else None
    if image_url:
        validate_uploaded_image_url(image_url, payload.image_id or "")
    return ImportRecipeCommand(
        user_id=user_id,
        language=language,
        url=payload.url.strip() if payload.url else None,
        text=payload.text if payload.text and payload.text.strip() else None,
        image_url=image_url,
        note=payload.note,
    )
