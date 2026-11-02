"""Supplement logging: a person's list, catalog search, barcode lookup and doses.

Every route answers 503 SUPPLEMENTS_UNAVAILABLE until ``SUPPLEMENTS_ENABLED``
is on and the tables are migrated. Like water, there is no subscription check
here; the app gates the screen with ``requireLapsedLifestyleAccess``.
"""

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exceptions import ValidationException
from src.api.middleware.rate_limit import limiter
from src.api.schemas.request.supplement_requests import (
    AddSupplementRequest,
    LogSupplementIntakeRequest,
    UpdateSupplementRequest,
)
from src.app.commands.supplement import (
    AddSupplementCommand,
    DeleteSupplementIntakeCommand,
    LogSupplementIntakeCommand,
    RemoveSupplementCommand,
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
from src.app.services.describe_log_replay import describe_key
from src.bootstrap.supplements import supplements_available
from src.domain.utils.fingerprint_utils import (
    canonicalize_fingerprint,
    normalize_idempotency_key,
)

SUPPLEMENT_INTAKE_OPERATION = "supplement_intake"
# Same budgets as food search and barcode lookup.
SEARCH_LIMIT = "30/minute"
BARCODE_LIMIT = "20/minute"


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
