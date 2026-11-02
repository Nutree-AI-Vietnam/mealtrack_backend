"""Supplement logging: a person's list, catalog search, barcode lookup and doses.

Every route answers 503 SUPPLEMENTS_UNAVAILABLE until ``SUPPLEMENTS_ENABLED``
is on and the tables are migrated. Like water, there is no subscription check
here; the app gates the screen with ``requireLapsedLifestyleAccess``.
"""

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from src.api.base_dependencies import (
    get_read_only_uow_factory,
    get_supplement_label_scan_quota,
)
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exceptions import ValidationException
from src.api.middleware.premium_check import require_subscription
from src.api.middleware.rate_limit import limiter
from src.api.routes.v1.meals_route_helpers import validate_uploaded_image_url
from src.api.schemas.request.supplement_requests import (
    AddSupplementRequest,
    LogSupplementIntakeRequest,
    ScanSupplementLabelRequest,
    UpdateSupplementRequest,
)
from src.app.commands.supplement import (
    AddSupplementCommand,
    DeleteSupplementIntakeCommand,
    LogSupplementIntakeCommand,
    RemoveSupplementCommand,
    ScanSupplementLabelCommand,
    UpdateSupplementCommand,
)
from src.app.handlers.command_handlers.supplement_command_handlers import (
    ADD_SUPPLEMENT_OPERATION,
)
from src.app.queries.supplement import (
    GetMySupplementsQuery,
    LookupSupplementBarcodeQuery,
    SearchSupplementsQuery,
)
from src.app.services.describe_log_replay import describe_key, stored_reply
from src.bootstrap.supplements import supplements_available
from src.domain.utils.fingerprint_utils import (
    canonicalize_fingerprint,
    normalize_idempotency_key,
)

SUPPLEMENT_INTAKE_OPERATION = "supplement_intake"
# Same budgets as food search and barcode lookup.
SEARCH_LIMIT = "30/minute"
BARCODE_LIMIT = "20/minute"
SUPPLEMENT_LABEL_SCAN_OPERATION = "supplement_label_scan"
LABEL_SCAN_LIMIT = "5/minute"


async def require_supplements() -> None:
    try:
        available = await supplements_available()
    except Exception as exc:
        available = False
        cause: Exception | None = exc
    else:
        cause = None
    if not available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "SUPPLEMENTS_UNAVAILABLE",
                "message": "Supplement logging is not available",
            },
        ) from cause


router = APIRouter(
    prefix="/v1/supplements",
    tags=["Supplements"],
    dependencies=[Depends(require_supplements)],
)


def _timezone(request: Request) -> str | None:
    return request.headers.get("X-Timezone")


@router.get("/mine")
async def get_my_supplements(
    request: Request,
    date_: date | None = Query(None, alias="date", description="YYYY-MM-DD"),
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
):
    """The list with the day's doses (the user's today by default)."""
    return await event_bus.send(
        GetMySupplementsQuery(
            user_id, target_date=date_, header_timezone=_timezone(request)
        )
    )


@router.get("/search")
@limiter.limit(SEARCH_LIMIT)
async def search_supplements(
    request: Request,
    q: str = Query(..., min_length=1, max_length=200),
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
):
    """Approved catalog rows (sold in Vietnam first), then the user's own."""
    return await event_bus.send(SearchSupplementsQuery(user_id, q))


@router.get("/barcode/{code}")
@limiter.limit(BARCODE_LIMIT)
async def lookup_supplement_barcode(
    request: Request,
    code: str,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
):
    """The shared product, else the user's own; 404 SUPPLEMENT_NOT_FOUND otherwise."""
    return await event_bus.send(LookupSupplementBarcodeQuery(user_id, code))


def _replay_key(operation: str, raw_key: str | None, body: Any) -> Any:
    try:
        key = normalize_idempotency_key(raw_key)
    except ValueError as exc:
        raise ValidationException(str(exc), "IDEMPOTENCY_KEY_INVALID") from exc
    return describe_key(
        operation, key, canonicalize_fingerprint(body.model_dump(mode="json"))
    )


@router.post("/mine", status_code=status.HTTP_201_CREATED)
async def add_supplement(
    body: AddSupplementRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Put a visible product on the list; ``log_now`` also logs one dose.

    The same Idempotency-Key replays the stored reply, so a retry never logs
    the dose twice or answers 409.
    """
    return await event_bus.send(
        AddSupplementCommand(
            user_id=user_id,
            product_id=body.product_id,
            units_per_dose=body.units_per_dose,
            log_now=body.log_now,
            intake_id=str(body.intake_id) if body.intake_id else None,
            taken_at=body.taken_at,
            header_timezone=_timezone(request),
            replay_key=_replay_key(ADD_SUPPLEMENT_OPERATION, idempotency_key, body),
        )
    )


@router.patch("/mine/{entry_id}")
async def update_supplement(
    entry_id: str,
    body: UpdateSupplementRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
):
    return await event_bus.send(
        UpdateSupplementCommand(
            user_id=user_id,
            entry_id=entry_id,
            units_per_dose=body.units_per_dose,
            active=body.active,
            sort_order=body.sort_order,
        )
    )


@router.delete("/mine/{entry_id}")
async def remove_supplement(
    entry_id: str,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
):
    """Take the product off the list; past doses stay."""
    return await event_bus.send(RemoveSupplementCommand(user_id, entry_id))


@router.post("/intakes", status_code=status.HTTP_201_CREATED)
async def log_supplement_intake(
    body: LogSupplementIntakeRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Log a dose; a reused ``intake_id`` answers 409 unless the key replays it."""
    replay_key = _replay_key(SUPPLEMENT_INTAKE_OPERATION, idempotency_key, body)
    return await event_bus.send(
        LogSupplementIntakeCommand(
            user_id=user_id,
            intake_id=str(body.intake_id),
            product_id=body.product_id,
            units=body.units,
            taken_at=body.taken_at,
            header_timezone=_timezone(request),
            replay_key=replay_key,
        )
    )


@router.delete("/intakes/{intake_id}")
async def delete_supplement_intake(
    intake_id: str,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
):
    """Undo or delete one of the user's doses."""
    return await event_bus.send(DeleteSupplementIntakeCommand(user_id, intake_id))


@router.post("/scan-label", status_code=status.HTTP_201_CREATED)
@limiter.limit(LABEL_SCAN_LIMIT)
async def scan_supplement_label(
    request: Request,
    body: ScanSupplementLabelRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
    quota: Any = Depends(get_supplement_label_scan_quota),
    replay_uow_factory: Any = Depends(get_read_only_uow_factory),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Read a label photo into the user's own product, list it and log a dose.

    ``route: "food"`` means the product was saved as a meal instead. Checks run
    rate limit, replay, Premium (402), then the daily quota (429).
    """
    validate_uploaded_image_url(body.image_url, body.image_id)
    replay_key = _replay_key(SUPPLEMENT_LABEL_SCAN_OPERATION, idempotency_key, body)
    if replay_key is None:
        raise ValidationException(
            "Idempotency-Key header is required", "IDEMPOTENCY_KEY_INVALID"
        )
    reply = await stored_reply(replay_uow_factory, user_id, replay_key)
    if reply is not None:
        return reply
    await require_subscription(user_id)
    await quota.consume(user_id, _timezone(request))
    return await event_bus.send(
        ScanSupplementLabelCommand(
            user_id=user_id,
            image_url=body.image_url,
            intake_id=str(body.intake_id),
            taken_at=body.taken_at,
            header_timezone=_timezone(request),
            replay_key=replay_key,
        )
    )
