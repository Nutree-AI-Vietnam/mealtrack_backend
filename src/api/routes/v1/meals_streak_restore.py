"""POST /v1/meals/streak/restore: restore missed streak days."""

from fastapi import APIRouter, Depends, Request

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.middleware.rate_limit import limiter
from src.api.schemas.request.streak_restore_requests import StreakRestoreRequest
from src.api.schemas.response.streak_restore_responses import StreakRestoreResponse
from src.app.commands.streak import PackClaim, RestoreStreakCommand
from src.infra.event_bus import EventBus

router = APIRouter()


@router.post("/streak/restore", response_model=StreakRestoreResponse)
@limiter.limit("10/minute")
async def restore_streak(
    request: Request,
    body: StreakRestoreRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Restore the missed days of a broken streak.

    Spends the free restore first, then held ones. With `purchase`, the pack
    the app just bought is verified with RevenueCat and credited in the same
    step; `dates` may then be empty to credit only. Not Premium-gated.
    """
    purchase = (
        PackClaim(
            product_id=body.purchase.product_id,
            transaction_id=body.purchase.transaction_id,
        )
        if body.purchase
        else None
    )
    return await event_bus.send(
        RestoreStreakCommand(
            user_id=user_id,
            dates=body.dates,
            purchase=purchase,
            header_timezone=request.headers.get("X-Timezone"),
        )
    )
