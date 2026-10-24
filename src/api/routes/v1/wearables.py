"""Cloud wearable sign-in through Open Wearables.

Phone sources stay on the device. Fitbit is excluded because its Web API
ends on 2026-10-30. Meals and water are not imported. A workout is listed
so the person can add it; it is never applied on its own.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
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

router = APIRouter(prefix="/v1/wearables", tags=["Wearables"])

CLOUD_WEARABLE_PROVIDERS = frozenset(
    {
        "garmin",
        "oura",
        "whoop",
        "polar",
        "suunto",
        "withings",
        "google_health",
        "ultrahuman",
        "strava",
    }
)

# Providers that can supply both numbers of today's overlay.
BALANCE_PROVIDERS = frozenset(
    {"garmin", "oura", "whoop", "polar", "suunto", "withings", "google_health"}
)

# Steps exist. Active calories do not, so burn stays empty for these.
STEPS_PROVIDERS = frozenset({"ultrahuman"})

# Per-activity calories. Listed for the person to add, never summed into the day.
WORKOUT_PROVIDERS = frozenset({"strava"})


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


def _redirect_uri(provider: str) -> str:
    # Android opens nutree links only with the open-nutree host; iOS takes any host.
    return f"nutree://open-nutree/wearables/connected/{provider}"


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


@router.get("/oauth/{provider}/authorize")
async def authorize_wearable(
    provider: str,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_async_db),
    client: OpenWearablesClient = Depends(open_wearables_client),
) -> dict[str, str]:
    _require_cloud_provider(provider)
    _require_configured()
    try:
        ow_user_id = await _linked_user_id(session, user_id)
        if ow_user_id is None:
            created = await client.create_user()
            ow_user_id = await _store_linked_user_id(session, user_id, created)
        authorization_url = await client.authorization_url(
            provider,
            ow_user_id,
            _redirect_uri(provider),
        )
    except OpenWearablesError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Wearable sign-in is unavailable",
        ) from exc
    return {"authorization_url": authorization_url}


@router.get("/connections")
async def list_wearable_connections(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_async_db),
    client: OpenWearablesClient = Depends(open_wearables_client),
) -> dict[str, list[dict[str, str]]]:
    _require_configured()
    ow_user_id = await _linked_user_id(session, user_id)
    if ow_user_id is None:
        return {"connections": []}
    try:
        connections = await client.connections(ow_user_id)
    except OpenWearablesError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Wearable connections are unavailable",
        ) from exc
    return {"connections": connections}


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
    day: str = Query(alias="date", min_length=10, max_length=10),
    provider: str = Query(min_length=1),
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
        steps, active = await client.activity_day(ow_user_id, day, provider)
    except OpenWearablesError:
        return _activity(provider)
    if provider in STEPS_PROVIDERS:
        active = 0.0
    return _activity(provider, steps=steps, active=active, connected=True)


@router.get("/workouts")
async def wearable_workouts(
    day: str = Query(alias="date", min_length=10, max_length=10),
    provider: str = Query(min_length=1),
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
        workouts = await client.workouts_on_day(ow_user_id, day, provider)
    except OpenWearablesError:
        return {"provider": provider, "workouts": []}
    return {"provider": provider, "workouts": workouts}
