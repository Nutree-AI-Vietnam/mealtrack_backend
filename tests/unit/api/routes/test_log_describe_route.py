"""POST and DELETE /v1/log/describe: guard order, replay, dates and undo errors."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from tests.fixtures.fakes.fake_describe_db import FakeDescribeDb

from src.api.base_dependencies import (
    get_async_food_reference_repository,
    get_describe_log_guard,
    get_read_only_uow_factory,
)
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exception_handlers import register_exception_handlers
from src.api.exceptions import ConflictException, ResourceNotFoundException
from src.api.middleware.rate_limit import (
    limiter,
    rate_limit_exceeded_handler,
    reset_limiter_counters,
)
from src.api.routes.v1.log_describe import router
from src.app.commands.describe_any import DescribeAnyCommand, UndoDescribeAnyCommand
from src.app.services import describe_log_replay as replay
from src.app.services.describe_log_guard import DescribeLogGuard
from src.domain.utils.fingerprint_utils import canonicalize_fingerprint

USER_ID = "44444444-4444-4444-4444-444444444444"
URL = "/v1/log/describe"
REPLY = {
    "saved": [
        {
            "kind": "drink",
            "id": "drink-1",
            "title": "Trà đá",
            "summary": "250 ml",
            "estimated": False,
        }
    ],
    "skipped": [],
    "nothing_found": False,
}


class Bus:
    def __init__(self):
        self.commands: list = []
        self.reply: dict | Exception = REPLY

    async def send(self, command):
        self.commands.append(command)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def env():
    reset_limiter_counters()
    db = FakeDescribeDb()
    state = SimpleNamespace(
        db=db,
        bus=Bus(),
        enabled=True,
        daily_limit=60,
        subscription=AsyncMock(return_value=None),
        counts=0,
    )

    async def count(key, ttl):
        state.counts += 1
        return state.counts

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_configured_event_bus] = lambda: state.bus
    app.dependency_overrides[get_read_only_uow_factory] = lambda: db.uow
    app.dependency_overrides[get_async_food_reference_repository] = lambda: None
    app.dependency_overrides[get_describe_log_guard] = lambda: DescribeLogGuard(
        enabled=state.enabled,
        daily_limit=state.daily_limit,
        require_subscription=state.subscription,
        increment_counter=count,
    )
    state.client = TestClient(app, raise_server_exceptions=False)
    yield state
    reset_limiter_counters()


def _core(response) -> dict:
    """The reply without the null payloads of the other kinds."""
    body = response.json()
    body["saved"] = [
        {key: value for key, value in item.items() if value is not None}
        for item in body["saved"]
    ]
    return body


def _post(env, text="1 ly trà đá", request_id="r-1", **body):
    return env.client.post(URL, json={"text": text, "request_id": request_id, **body})


def test_a_sentence_reaches_the_handler_with_every_kind_by_default(env):
    response = _post(env, local_date="2026-11-01", local_time="07:30")

    assert response.status_code == 200, response.text
    assert _core(response) == REPLY
    [command] = env.bus.commands
    assert isinstance(command, DescribeAnyCommand)
    assert command.kinds == frozenset({"food", "drink", "activity", "supplement"})
    assert command.target_date.isoformat() == "2026-11-01"
    assert command.local_time.isoformat() == "07:30:00"
    assert command.idempotency_key == "r-1" and command.request_fingerprint


def test_kinds_narrow_the_sentence(env):
    _post(env, kinds=["supplement"])

    assert env.bus.commands[0].kinds == frozenset({"supplement"})


def test_switch_off_gives_503_before_premium(env):
    env.enabled = False

    response = _post(env)

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "FEATURE_DISABLED"
    env.subscription.assert_not_called()
    assert env.bus.commands == []


def test_non_premium_gives_402(env):
    env.subscription = AsyncMock(
        side_effect=HTTPException(
            status_code=402, detail={"error_code": "SUBSCRIPTION_REQUIRED"}
        )
    )

    response = _post(env)

    assert response.status_code == 402
    assert env.bus.commands == []


def test_an_exhausted_daily_quota_gives_429(env):
    env.daily_limit = 1

    first, second = _post(env, request_id="a"), _post(env, request_id="b")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["error_code"] == "DESCRIBE_DAILY_LIMIT"
    assert len(env.bus.commands) == 1


@pytest.mark.asyncio
async def test_a_finished_sentence_replays_before_the_guard(env):
    fingerprint = canonicalize_fingerprint(
        {
            "text": "1 ly trà đá",
            "local_date": None,
            "kinds": ["activity", "drink", "food", "supplement"],
        }
    )
    key = replay.DescribeKey(replay.LOG_DESCRIBE_OPERATION, "r-1", fingerprint)
    reservation, _ = await replay.claim(env.db.uow, USER_ID, key, lease_seconds=60)
    async with env.db.uow() as uow:
        await replay.complete(uow, reservation, REPLY, {"drink": ["drink-1"]})
    env.enabled = False

    response = _post(env, local_time="23:59")

    assert response.status_code == 200
    assert _core(response) == REPLY  # the undo ids never leave the server
    assert "_created" not in response.json()
    assert env.bus.commands == []


@pytest.mark.parametrize(
    ("field", "value"), [("local_date", "01/11/2026"), ("local_time", "7h30")]
)
def test_bad_dates_and_times_give_400_before_the_guard(env, field, value):
    response = _post(env, **{field: value})

    assert response.status_code == 400
    env.subscription.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        {"text": "x" * 301, "request_id": "r"},
        {"text": "trà", "request_id": ""},
        {"text": "trà", "request_id": "r", "kinds": ["meal"]},
        {"text": "trà", "request_id": "r", "kinds": []},
    ],
)
def test_malformed_requests_are_refused(env, body):
    assert env.client.post(URL, json=body).status_code == 422


def test_undo_sends_the_request_id(env):
    env.bus.reply = {"request_id": "r-1", "deleted": {"food": 1, "drink": 2}}

    response = env.client.delete(f"{URL}/r-1")

    assert response.status_code == 200
    assert response.json()["deleted"] == {"food": 1, "drink": 2}
    [command] = env.bus.commands
    assert isinstance(command, UndoDescribeAnyCommand)
    assert (command.user_id, command.request_id) == (USER_ID, "r-1")


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ResourceNotFoundException("gone", "DESCRIBE_REQUEST_NOT_FOUND"), 404),
        (ConflictException("busy", "IDEMPOTENCY_KEY_IN_PROGRESS"), 409),
    ],
)
def test_undo_errors_keep_their_status(env, error, status):
    env.bus.reply = error

    assert env.client.delete(f"{URL}/r-1").status_code == status


def test_a_blank_undo_id_is_refused(env):
    assert env.client.delete(f"{URL}/%20").status_code == 400
    assert env.bus.commands == []


def test_a_saved_sentence_answers_even_when_its_meal_cannot_be_read(env):
    env.bus.reply = {
        "saved": [
            {
                "kind": "food",
                "id": "meal-1",
                "title": "Phở bò",
                "summary": "389 kcal",
                "estimated": False,
            }
        ],
        "skipped": [],
        "nothing_found": False,
    }

    # The fake store has no meals repository, so the read fails.
    response = _post(env)

    assert response.status_code == 200
    assert response.json()["saved"][0]["meal"] is None
