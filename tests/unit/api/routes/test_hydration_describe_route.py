"""POST /v1/hydration/describe: guard order, Idempotency-Key replay, date rule."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from src.api.base_dependencies import get_describe_log_guard
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exception_handlers import register_exception_handlers
from src.api.middleware import premium_check
from src.api.middleware.rate_limit import (
    limiter,
    rate_limit_exceeded_handler,
    reset_limiter_counters,
)
from src.api.routes.v1.hydration import router
from src.api.services import describe_log_requests
from src.app.handlers.command_handlers.describe_hydration_command_handler import (
    DescribeHydrationCommandHandler,
)
from src.app.services.describe_log_guard import DescribeLogGuard
from src.domain.ports.subscription_service_port import EntitlementCheck
from src.domain.utils.timezone_utils import utc_now
from src.infra.services.durable_write_service import (
    DurableWriteConflictError,
    DurableWriteInProgressError,
    DurableWriteRecord,
)

USER_ID = "33333333-3333-3333-3333-333333333333"
URL = "/v1/hydration/describe"


class ReplayStore:
    """In-memory claim-before-create store with the durable write semantics."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], dict] = {}

    async def begin(self, *, user_id, action, idempotency_key, request_fingerprint):
        row = self.rows.get((user_id, action, idempotency_key))
        if row is None:
            self.rows[(user_id, action, idempotency_key)] = {
                "fingerprint": request_fingerprint,
                "body": None,
            }
            return None
        if row["fingerprint"] != request_fingerprint:
            raise DurableWriteConflictError
        if row["body"] is None:
            raise DurableWriteInProgressError
        return DurableWriteRecord(request_fingerprint, 200, row["body"], None)

    async def complete(self, *, user_id, action, idempotency_key, response_body, **_):
        self.rows[(user_id, action, idempotency_key)]["body"] = response_body
        return DurableWriteRecord("", 200, response_body, None)

    async def abandon(self, *, user_id, action, idempotency_key, **_):
        self.rows.pop((user_id, action, idempotency_key), None)


class _Entries:
    def __init__(self, saved):
        self.saved = saved

    async def add(self, entry):
        self.saved.append(entry)
        return entry


class _Uow:
    def __init__(self, saved):
        self.meals = SimpleNamespace(save=AsyncMock(side_effect=lambda meal: meal))
        self.hydration_entries = _Entries(saved)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class HandlerBus:
    """Runs the real describe handler on fakes, counting model calls."""

    def __init__(self, reply):
        self.saved: list = []
        self.generation = AsyncMock()
        self.generation.generate_meal_plan_async = AsyncMock(return_value=reply)
        self.handler = DescribeHydrationCommandHandler(
            uow_factory=lambda: _Uow(self.saved),
            meal_generation_service=self.generation,
            event_publisher=AsyncMock(),
        )

    async def send(self, command):
        return await self.handler.handle(command)


@pytest.fixture
def env(monkeypatch):
    reset_limiter_counters()
    store = ReplayStore()
    monkeypatch.setattr(describe_log_requests, "begin_durable_write", store.begin)
    monkeypatch.setattr(describe_log_requests, "complete_durable_write", store.complete)
    monkeypatch.setattr(describe_log_requests, "abandon_durable_write", store.abandon)
    state = SimpleNamespace(
        store=store,
        enabled=True,
        subscription=AsyncMock(return_value=None),
        counts=0,
        bus=HandlerBus(
            {"items": [{"catalog_id": "tea", "name": "trà đá", "servings": 1}]}
        ),
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
    app.dependency_overrides[get_describe_log_guard] = lambda: DescribeLogGuard(
        enabled=state.enabled,
        daily_limit=60,
        require_subscription=state.subscription,
        increment_counter=count,
    )
    state.client = TestClient(app, raise_server_exceptions=False)
    yield state
    reset_limiter_counters()


def _post(env, text="1 ly trà đá", key="key-1", **body):
    headers = {"Idempotency-Key": key} if key else {}
    return env.client.post(URL, json={"text": text, **body}, headers=headers)


def test_same_key_saves_once_and_replays_the_same_entries(env):
    first = _post(env)
    second = _post(env)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert [entry["drink_id"] for entry in first.json()["entries"]] == ["tea"]
    assert len(env.bus.saved) == 1
    env.bus.generation.generate_meal_plan_async.assert_awaited_once()


def test_same_key_with_another_sentence_is_a_conflict(env):
    _post(env)

    response = _post(env, text="2 ly trà đá")

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert len(env.bus.saved) == 1


def test_failed_sentence_drops_its_claim_so_a_retry_runs(env):
    env.bus.generation.generate_meal_plan_async.return_value = {"items": []}

    first = _post(env)
    env.bus.generation.generate_meal_plan_async.return_value = {
        "items": [{"catalog_id": "water", "name": "nước"}]
    }
    second = _post(env)

    assert first.status_code == 422
    assert first.json()["detail"]["error_code"] == "DESCRIBE_NOTHING_RECOGNIZED"
    assert second.status_code == 200
    assert len(env.bus.saved) == 1


def test_invalid_ai_reply_after_the_correction_gives_422(env):
    env.bus.generation.generate_meal_plan_async.return_value = {
        "items": [{"catalog_id": "tea"}]
    }

    response = _post(env)

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "AI_OUTPUT_INVALID"
    assert env.bus.generation.generate_meal_plan_async.await_count == 2
    assert env.store.rows == {}


def test_ai_outage_gives_503_and_saves_nothing(env):
    env.bus.generation.generate_meal_plan_async.side_effect = ConnectionError("down")

    response = _post(env)

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "AI_UNAVAILABLE"
    assert env.bus.saved == [] and env.store.rows == {}


def test_failure_after_the_model_keeps_the_claim(env, monkeypatch):
    """A save that may have committed must not be retried under the same key."""

    async def lost_commit(command):
        raise RuntimeError("connection lost during COMMIT")

    monkeypatch.setattr(env.bus, "send", lost_commit)

    first = _post(env)
    second = _post(env)

    assert first.status_code == 500
    assert second.status_code == 409
    assert second.json()["detail"]["error_code"] == "IDEMPOTENCY_KEY_IN_PROGRESS"


def test_missing_key_is_refused_before_the_guard(env):
    response = _post(env, key=None)

    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "IDEMPOTENCY_KEY_INVALID"
    env.subscription.assert_not_called()


def test_switch_off_gives_503_before_premium(env):
    env.enabled = False

    response = _post(env)

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "FEATURE_DISABLED"
    env.subscription.assert_not_called()
    assert env.bus.saved == []


def test_non_premium_gives_402(env, monkeypatch):
    uow = SimpleNamespace(
        subscriptions=SimpleNamespace(
            has_entitled_subscription=AsyncMock(return_value=False)
        ),
        users=SimpleNamespace(
            find_by_id=AsyncMock(return_value=SimpleNamespace(firebase_uid="fb-1"))
        ),
    )

    class _ReadOnlyUow:
        async def __aenter__(self):
            return uow

        async def __aexit__(self, *exc):
            return False

    revenuecat = SimpleNamespace(
        check_entitlement=AsyncMock(return_value=EntitlementCheck.NOT_ENTITLED)
    )
    monkeypatch.setenv("REVENUECAT_SECRET_API_KEY", "key")
    monkeypatch.setattr(premium_check, "_get_uow_factory", lambda: _ReadOnlyUow)
    monkeypatch.setattr(premium_check, "_get_cache", lambda: None)
    monkeypatch.setattr(premium_check, "_get_subscription_service", lambda: revenuecat)
    env.subscription = premium_check.require_subscription

    response = _post(env)

    assert response.status_code == 402
    assert response.json()["detail"]["error_code"] == "SUBSCRIPTION_REQUIRED"
    assert env.bus.saved == []


def test_limiter_runs_before_premium(env):
    env.subscription = AsyncMock(side_effect=HTTPException(status_code=402))

    codes = [_post(env, key=f"key-{n}").status_code for n in range(21)]

    assert codes == [402] * 20 + [429]
    assert env.subscription.await_count == 20


def test_date_after_tomorrow_gives_400(env):
    too_late = (utc_now().date() + timedelta(days=2)).isoformat()

    response = _post(env, target_date=too_late)

    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "INVALID_DATE"
    env.bus.generation.generate_meal_plan_async.assert_not_called()


def test_sentence_longer_than_300_characters_is_refused(env):
    assert _post(env, text="n" * 301).status_code == 422
