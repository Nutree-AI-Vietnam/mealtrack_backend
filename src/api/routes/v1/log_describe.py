"""The + panel's Ghi nhanh: one sentence logs food, drinks, activities and supplements.

POST replays a finished ``request_id`` first, then runs the Ghi nhanh guard
(switch, Premium, daily quota) and saves everything in one transaction.
Supplements are skipped, not refused, until supplement logging is on. DELETE
undoes the whole sentence in one transaction.
"""

import logging
from datetime import date, datetime, time
from typing import Any

from fastapi import APIRouter, Depends, Request

from src.api.base_dependencies import (
    get_async_food_reference_repository,
    get_describe_log_guard,
    get_read_only_uow_factory,
)
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exceptions import ValidationException
from src.api.mappers.meal_mapper import MealMapper
from src.api.middleware.accept_language import get_request_language
from src.api.middleware.rate_limit import limiter
from src.api.routes.v1.meals_route_helpers import (
    load_food_reference_display_projections,
)
from src.api.schemas.request.log_describe_requests import DescribeAnyRequest
from src.api.schemas.response.describe_log_responses import (
    DescribeAnyResponse,
    UndoDescribeAnyResponse,
)
from src.api.services.describe_log_requests import run_describe
from src.app.commands.describe_any import DescribeAnyCommand, UndoDescribeAnyCommand
from src.app.services.describe_any_items import ALL_KINDS, FOOD
from src.app.services.describe_log_guard import DescribeLogGuard
from src.app.services.describe_log_replay import LOG_DESCRIBE_OPERATION, DescribeKey
from src.domain.utils.fingerprint_utils import normalize_idempotency_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/log", tags=["Log"])


@router.post("/describe", response_model=DescribeAnyResponse)
@limiter.limit("20/minute")
async def describe_any(
    request: Request,
    body: DescribeAnyRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
    guard: DescribeLogGuard = Depends(get_describe_log_guard),
    replay_uow_factory: Any = Depends(get_read_only_uow_factory),
    food_references: Any = Depends(get_async_food_reference_repository),
) -> DescribeAnyResponse:
    """Save every food, drink, activity and supplement in one sentence (Premium).

    A sentence with food runs a second model call, so it can take about 20 s.
    """
    target_date = _local_date(body.local_date)
    local_time = _local_time(body.local_time)
    kinds = frozenset(body.kinds or ALL_KINDS)
    header_timezone = request.headers.get("X-Timezone")
    language = get_request_language(request)

    async def send(sentence: str, key: DescribeKey) -> dict:
        return await event_bus.send(
            DescribeAnyCommand(
                user_id=user_id,
                text=sentence,
                kinds=kinds,
                target_date=target_date,
                local_time=local_time,
                header_timezone=header_timezone,
                language=language,
                idempotency_key=key.idempotency_key,
                request_fingerprint=key.request_fingerprint,
            )
        )

    reply = await run_describe(
        user_id=user_id,
        text=body.text,
        timezone=header_timezone,
        idempotency_key=body.request_id,
        operation=LOG_DESCRIBE_OPERATION,
        # The clock only picks the meal type, so a retry a minute later still replays.
        fingerprint={"local_date": body.local_date, "kinds": sorted(kinds)},
        guard=guard,
        replay_uow_factory=replay_uow_factory,
        send=send,
        response_model=DescribeAnyResponse,
    )
    try:
        await _attach_meals(reply, replay_uow_factory, food_references, language)
    except Exception:
        # Everything is saved; the app reloads the day when the meal is missing.
        logger.warning("Describe any meal detail failed", exc_info=True)
    return reply


@router.delete("/describe/{request_id}", response_model=UndoDescribeAnyResponse)
async def undo_describe_any(
    request_id: str,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
) -> UndoDescribeAnyResponse:
    """Hoàn tác: delete everything the sentence saved, in one transaction.

    404 DESCRIBE_REQUEST_NOT_FOUND when no finished sentence has this id; 409
    while it is still being logged. A sentence that saved nothing, or a
    second undo, deletes nothing and answers zero counts.
    """
    try:
        key = normalize_idempotency_key(request_id)
    except ValueError as exc:
        raise ValidationException(str(exc), "IDEMPOTENCY_KEY_INVALID") from exc
    if key is None:
        raise ValidationException("request_id is required", "IDEMPOTENCY_KEY_INVALID")
    result = await event_bus.send(UndoDescribeAnyCommand(user_id, key))
    return UndoDescribeAnyResponse.model_validate(result)


async def _attach_meals(
    reply: DescribeAnyResponse,
    uow_factory: Any,
    food_references: Any,
    language: str,
) -> None:
    """Food items carry the meal as the manual meal route returns it, read
    fresh so a replay shows later edits; a meal deleted since stays null."""
    foods = [item for item in reply.saved if item.kind == FOOD]
    if not foods:
        return
    async with uow_factory() as uow:
        meals = {item.id: await uow.meals.find_by_id(item.id) for item in foods}
    for item in foods:
        meal = meals.get(item.id)
        if meal is None:
            continue
        item.meal = MealMapper.to_detailed_response(
            meal,
            image_url=getattr(getattr(meal, "image", None), "url", None),
            target_language=language,
            display_name_by_food_reference=await load_food_reference_display_projections(
                meal, food_references, language=language
            ),
        )


def _local_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValidationException(
            "Invalid local_date. Use YYYY-MM-DD", "INVALID_DATE"
        ) from exc


def _local_time(raw: str | None) -> time | None:
    if raw is None:
        return None
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except ValueError as exc:
        raise ValidationException(
            "Invalid local_time. Use HH:MM", "INVALID_TIME"
        ) from exc
