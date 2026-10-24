"""Cloud wearable sign-in through Open Wearables.

Phone sources stay on the device. Fitbit is excluded because its Web API
ends on 2026-10-30. Meals and water are not imported. A workout is listed
so the person can add it; it is never applied on its own.
"""

import asyncio
import logging
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.api.routes.v1.wearables_route_support import (
    BALANCE_PROVIDERS,
    CLOUD_WEARABLE_PROVIDERS,
    STEPS_PROVIDERS,
    WORKOUT_PROVIDERS,
    ReturnTo,
    connection_rows,
    has_client_id,
    local_day_bounds,
    redirect_uri,
    signable_providers,
)
from src.infra.adapters.open_wearables_client import (
    OpenWearablesClient,
    OpenWearablesError,
)
from src.infra.config.settings import settings
from src.infra.database.config_async import get_async_db
from src.infra.repositories.wearable_account_repository import (
    load_open_wearables_user_id,
    save_open_wearables_user_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/wearables", tags=["Wearables"])

_NOT_AVAILABLE = "This wearable is not available yet"


def wearable_sign_in_ready() -> bool:
    return bool(
        settings.OPEN_WEARABLES_API_URL
        and settings.OPEN_WEARABLES_API_KEY
        and settings.OPEN_WEARABLES_APP_ID
        and settings.OPEN_WEARABLES_APP_SECRET
    )


def open_wearables_client() -> OpenWearablesClient:
    return OpenWearablesClient(
        settings.OPEN_WEARABLES_API_URL,
        settings.OPEN_WEARABLES_API_KEY,
    )


def _signable() -> frozenset[str]:
    if not wearable_sign_in_ready():
        return frozenset()
    return signable_providers(settings.OPEN_WEARABLES_PROVIDERS)


def _require_cloud_provider(provider: str) -> None:
    if provider not in CLOUD_WEARABLE_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This wearable does not use cloud sign-in",
        )


def _require_configured() -> None:
    if not wearable_sign_in_ready():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Wearable sign-in is not configured",
        )


async def _linked_user_id(session: AsyncSession, user_id: str) -> str | None:
    try:
        return await load_open_wearables_user_id(session, user_id)
    finally:
        await _end_transaction(session)


async def _store_linked_user_id(
    session: AsyncSession,
    user_id: str,
    open_wearables_user_id: str,
) -> str:
    try:
        return await save_open_wearables_user_id(
            session, user_id, open_wearables_user_id
        )
    finally:
        await _end_transaction(session)


async def _end_transaction(session: AsyncSession) -> None:
    """Return the pooled connection before the route calls Open Wearables.

    ``get_async_db`` keeps the session until the response is sent. Without
    this, the connection stays checked out for the whole Open Wearables round
    trip, and a burst of these requests drains the pool.
    """
    await session.rollback()


async def _sync_states_or_empty(
    client: OpenWearablesClient, open_wearables_user_id: str
) -> dict[str, dict[str, str | None]]:
    try:
        return await client.sync_states(open_wearables_user_id)
    except OpenWearablesError:
        logger.warning("Open Wearables sync runs are unavailable", exc_info=True)
        return {}


@router.get("/providers")
async def list_wearable_providers(
    _user_id: str = Depends(get_current_user_id),
) -> dict[str, list[dict[str, object]]]:
    """Cloud providers, and whether each one can sign in now."""
    signable = _signable()
    return {
        "providers": [
            {"provider": provider, "available": provider in signable}
            for provider in sorted(CLOUD_WEARABLE_PROVIDERS)
        ]
    }


@router.get("/oauth/{provider}/authorize")
async def authorize_wearable(
    provider: str,
    return_to: ReturnTo = Query(default="link"),
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_async_db),
    client: OpenWearablesClient = Depends(open_wearables_client),
) -> dict[str, str]:
    _require_cloud_provider(provider)
    _require_configured()
    if provider not in _signable():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NOT_AVAILABLE)
    try:
        ow_user_id = await _linked_user_id(session, user_id)
        if ow_user_id is None:
            created = await client.create_user()
            ow_user_id = await _store_linked_user_id(session, user_id, created)
        authorization_url = await client.authorization_url(
            provider,
            ow_user_id,
            redirect_uri(provider, return_to),
        )
    except OpenWearablesError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Wearable sign-in is unavailable",
        ) from exc
    if not has_client_id(authorization_url):
        logger.error(
            "Open Wearables has no sign-in app for %s. Register it there or "
            "remove it from OPEN_WEARABLES_PROVIDERS.",
            provider,
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NOT_AVAILABLE)
    return {"authorization_url": authorization_url}


@router.get("/connections")
async def list_wearable_connections(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_async_db),
    client: OpenWearablesClient = Depends(open_wearables_client),
) -> dict[str, list[dict[str, str | None]]]:
    _require_configured()
    ow_user_id = await _linked_user_id(session, user_id)
    if ow_user_id is None:
        return {"connections": []}
    try:
        connections, sync_states = await asyncio.gather(
            client.connections(ow_user_id),
            _sync_states_or_empty(client, ow_user_id),
        )
    except OpenWearablesError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Wearable connections are unavailable",
        ) from exc
    return {"connections": connection_rows(connections, sync_states)}


@router.delete("/connections/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_wearable(
    provider: str,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_async_db),
    client: OpenWearablesClient = Depends(open_wearables_client),
) -> Response:
    """Revoke the sign-in and delete what Open Wearables synced from it."""
    _require_cloud_provider(provider)
    _require_configured()
    ow_user_id = await _linked_user_id(session, user_id)
    if ow_user_id is not None:
        try:
            await client.disconnect(ow_user_id, provider)
        except OpenWearablesError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Wearable disconnect is unavailable",
            ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _activity(
    provider: str,
    *,
    steps: int = 0,
    active: float = 0.0,
    connected: bool = False,
) -> dict[str, object]:
    return {
        "provider": provider,
        "steps": steps,
        "active_calories_kcal": active,
        "connected": connected,
    }


@router.get("/activity")
async def wearable_activity(
    day: date = Query(alias="date"),
    provider: str = Query(min_length=1),
    timezone: str | None = Header(default=None, alias="X-Timezone"),
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_async_db),
    client: OpenWearablesClient = Depends(open_wearables_client),
) -> dict[str, object]:
    """Steps and active calories for one provider. Resting burn is excluded."""
    if provider not in BALANCE_PROVIDERS and provider not in STEPS_PROVIDERS:
        return _activity(provider)
    if not wearable_sign_in_ready():
        return _activity(provider)
    ow_user_id = await _linked_user_id(session, user_id)
    if ow_user_id is None:
        return _activity(provider)
    try:
        steps, active = await client.activity_day(
            ow_user_id, local_day_bounds(day, timezone), provider
        )
    except OpenWearablesError:
        return _activity(provider)
    if provider in STEPS_PROVIDERS:
        active = 0.0
    return _activity(provider, steps=steps, active=active, connected=True)


@router.get("/workouts")
async def wearable_workouts(
    day: date = Query(alias="date"),
    provider: str = Query(min_length=1),
    timezone: str | None = Header(default=None, alias="X-Timezone"),
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_async_db),
    client: OpenWearablesClient = Depends(open_wearables_client),
) -> dict[str, object]:
    """Activities the person can add. They are not written into the day here."""
    if provider not in WORKOUT_PROVIDERS or not wearable_sign_in_ready():
        return {"provider": provider, "workouts": []}
    ow_user_id = await _linked_user_id(session, user_id)
    if ow_user_id is None:
        return {"provider": provider, "workouts": []}
    try:
        workouts = await client.workouts_on_day(
            ow_user_id, local_day_bounds(day, timezone), provider
        )
    except OpenWearablesError:
        return {"provider": provider, "workouts": []}
    return {"provider": provider, "workouts": workouts}
