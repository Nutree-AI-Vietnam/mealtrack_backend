"""
Ingredient recognition API endpoints.
"""

from fastapi import APIRouter, Depends, Request

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.dependencies.subscription_access import require_active_subscription
from src.api.middleware.accept_language import get_request_language
from src.api.schemas.request import IngredientRecognitionRequest
from src.api.schemas.response import IngredientRecognitionResponse
from src.api.utils.json_body import json_body_openapi, read_json_body
from src.app.commands.ingredient import RecognizeIngredientCommand
from src.infra.event_bus import EventBus

router = APIRouter(prefix="/v1/ingredients", tags=["Ingredients"])


@router.post(
    "/recognize",
    response_model=IngredientRecognitionResponse,
    dependencies=[Depends(require_active_subscription)],
    # The photo is read only after authentication and the subscription
    # check, which a body parameter would come after.
    openapi_extra=json_body_openapi(IngredientRecognitionRequest),
)
async def recognize_ingredient(
    http_request: Request,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Recognize a food ingredient from an image.

    Takes a base64 encoded image and uses the configured vision AI provider to identify
    the primary food ingredient visible in the image.
    Respects Accept-Language header for translating the ingredient name.

    Returns:
    - name: Identified ingredient name (translated to user's language)
    - confidence: Confidence score between 0 and 1
    - category: Category (vegetable, fruit, protein, grain, dairy, seasoning, other)
    - success: Whether recognition was successful
    - message: Additional message (e.g., error details)
    """
    request = await read_json_body(http_request, IngredientRecognitionRequest)
    language = get_request_language(http_request)
    command = RecognizeIngredientCommand(
        image_data=request.image_data,
        language=language,
    )
    result = await event_bus.send(command)
    return IngredientRecognitionResponse(**result)


@router.get("/health")
async def ingredients_health():
    """Check if ingredient recognition service is healthy."""
    return {
        "status": "healthy",
        "service": "ingredient_recognition",
        "features": [
            "photo_ingredient_identification",
            "vision_ai",
            "confidence_scoring",
        ],
    }
