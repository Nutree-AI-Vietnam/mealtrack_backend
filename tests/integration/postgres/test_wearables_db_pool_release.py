"""Wearables routes return their DB connection before calling Open Wearables.

The routes read the link through ``get_async_db``, which keeps the session until
the response is sent. The fake client counts checked-out connections as each
Open Wearables call starts.
"""

from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.dependencies.auth import get_current_user_id
from src.api.routes.v1 import wearables as wearables_mod
from src.infra.adapters.open_wearables_client import OpenWearablesClient
from src.infra.database import config_async
from src.infra.database.models.user.user import User

pytestmark = pytest.mark.integration

DAY = "2026-10-24"
AUTHORIZATION_URL = "https://connect.garmin.com/start"


class _PoolCountingClient(OpenWearablesClient):
    def __init__(self, engine) -> None:
        super().__init__("http://ow.test", "test-key")
        self._pool = engine.sync_engine.pool
        self.checked_out: dict[str, int] = {}
        self.authorized_ids: list[str] = []

    def _count(self, call: str) -> None:
        self.checked_out[call] = self._pool.checkedout()

    async def create_user(self) -> str:
        self._count("create_user")
        return "ow-created"

    async def authorization_url(self, provider, user_id, redirect_uri) -> str:
        self._count("authorization_url")
        self.authorized_ids.append(user_id)
        return AUTHORIZATION_URL

    async def connections(self, user_id):
        self._count("connections")
        return [{"provider": "garmin", "status": "active"}]

    async def activity_day(self, user_id, day, provider):
        self._count("activity_day")
        return 4200, 210.0

    async def workouts_on_day(self, user_id, day, provider):
        self._count("workouts_on_day")
        return []


@pytest_asyncio.fixture
async def engine(migrated_database):
    engine = create_async_engine(migrated_database)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _seed_user(session: AsyncSession, open_wearables_user_id: str | None) -> str:
    user_id = str(uuid4())
    session.add(
        User(
            id=user_id,
            firebase_uid=user_id,
            email=f"{user_id}@example.test",
            username=user_id,
            password_hash="test",
            open_wearables_user_id=open_wearables_user_id,
        )
    )
    await session.commit()
    return user_id


async def _get(monkeypatch, engine, ow_client, user_id, path, params=None):
    # The real get_async_db runs, drawing from the pool the client counts.
    monkeypatch.setattr(
        config_async,
        "AsyncSessionLocal",
        async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False),
    )
    monkeypatch.setattr(wearables_mod, "wearable_sign_in_ready", lambda: True)
    app = FastAPI()
    app.include_router(wearables_mod.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[wearables_mod.open_wearables_client] = lambda: ow_client
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, params=params)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "params", "call"),
    [
        ("/v1/wearables/connections", None, "connections"),
        ("/v1/wearables/activity", {"date": DAY, "provider": "garmin"}, "activity_day"),
        (
            "/v1/wearables/workouts",
            {"date": DAY, "provider": "strava"},
            "workouts_on_day",
        ),
        ("/v1/wearables/oauth/garmin/authorize", None, "authorization_url"),
    ],
)
async def test_linked_user_routes_release_the_connection_first(
    pg_session, engine, monkeypatch, path, params, call
):
    user_id = await _seed_user(pg_session, str(uuid4()))
    ow_client = _PoolCountingClient(engine)

    response = await _get(monkeypatch, engine, ow_client, user_id, path, params)

    assert response.status_code == 200
    assert ow_client.checked_out == {call: 0}


@pytest.mark.asyncio
async def test_first_sign_in_releases_the_connection_before_each_call(
    pg_session, engine, monkeypatch
):
    user_id = await _seed_user(pg_session, None)
    ow_client = _PoolCountingClient(engine)

    response = await _get(
        monkeypatch, engine, ow_client, user_id, "/v1/wearables/oauth/garmin/authorize"
    )

    assert response.json() == {"authorization_url": AUTHORIZATION_URL}
    assert ow_client.checked_out == {"create_user": 0, "authorization_url": 0}
    assert ow_client.authorized_ids == ["ow-created"]
    stored = await pg_session.scalar(
        select(User.open_wearables_user_id).where(User.id == user_id)
    )
    assert stored == "ow-created"


@pytest.mark.asyncio
async def test_sign_in_that_loses_the_create_race_releases_the_reread(
    pg_session, engine, monkeypatch
):
    # Another request stores its id while this one creates a user. The save
    # then returns the stored id from a read and commits nothing.
    user_id = await _seed_user(pg_session, None)
    first_id = str(uuid4())

    class _RacedClient(_PoolCountingClient):
        async def create_user(self) -> str:
            created = await super().create_user()
            await pg_session.execute(
                update(User)
                .where(User.id == user_id)
                .values(open_wearables_user_id=first_id)
            )
            await pg_session.commit()
            return created

    ow_client = _RacedClient(engine)

    response = await _get(
        monkeypatch, engine, ow_client, user_id, "/v1/wearables/oauth/garmin/authorize"
    )

    assert response.json() == {"authorization_url": AUTHORIZATION_URL}
    assert ow_client.checked_out == {"create_user": 0, "authorization_url": 0}
    assert ow_client.authorized_ids == [first_id]
