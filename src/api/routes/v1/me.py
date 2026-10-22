"""Authenticated current-user endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Response

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.schemas.response.subscription_access import MyEntitlementsResponse
from src.app.queries.subscription_access import GetMyEntitlementsQuery
from src.app.services.subscription_access_flags import (
    database_subscription_access_enabled,
)
from src.domain.services.subscription_access_policy import (
    SubscriptionAccessUnavailable,
)

router = APIRouter(prefix="/v1/me", tags=["Me"])


@router.get("/entitlements", response_model=MyEntitlementsResponse)
async def get_my_entitlements(
    response: Response,
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
) -> MyEntitlementsResponse:
    """Return the database-owned access decision for the signed-in user."""
    response.headers["Cache-Control"] = "private, no-store"
    if not database_subscription_access_enabled():
        raise HTTPException(
            status_code=503,
            detail={"error_code": "ENTITLEMENTS_UNAVAILABLE"},
        )
    try:
        result = await event_bus.send(GetMyEntitlementsQuery(user_id))
    except SubscriptionAccessUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "ENTITLEMENTS_UNAVAILABLE"},
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "ENTITLEMENTS_UNAVAILABLE"},
        ) from error
    return MyEntitlementsResponse(**result)
