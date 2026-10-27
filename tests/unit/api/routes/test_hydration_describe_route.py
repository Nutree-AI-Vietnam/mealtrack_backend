"""POST /v1/hydration/describe: guard order, Idempotency-Key retries, date rule.

The real describe handler runs on a transactional in-memory store, so each
retry case sees what a database would: a reply stored with the entries, a
claim released or left to expire, a commit that fails before or after it
applies.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from tests.fixtures.fakes.fake_describe_db import FakeDescribeDb

from src.api.base_dependencies import get_describe_log_guard, get_read_only_uow_factory
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
from src.app.handlers.command_handlers.describe_hydration_command_handler import (
    DescribeHydrationCommandHandler,
)
from src.app.services import describe_log_replay
from src.app.services.describe_log_guard import DescribeLogGuard
from src.domain.ports.subscription_service_port import EntitlementCheck
from src.domain.utils.timezone_utils import utc_now

USER_ID = "33333333-3333-3333-3333-333333333333"
URL = "/v1/hydration/describe"
TEA = {"items": [{"catalog_id": "tea", "name": "trà đá", "servings": 1}]}


class HandlerBus:
    """Runs the real describe handler on the fake store, counting model calls."""

    def __init__(self, db: FakeDescribeDb):
        self.generation = AsyncMock()
        self.generation.generate_meal_plan_async = AsyncMock(return_value=TEA)
        self.handler = DescribeHydrationCommandHandler(
            uow_factory=db.uow,
            meal_generation_service=self.generation,
            event_publisher=AsyncMock(),
        )

    async def send(self, command):
        return await self.handler.handle(command)


@pytest.fixture
def env():
    reset_limiter_counters()
    db = FakeDescribeDb()
    state = SimpleNamespace(
        db=db,
        enabled=True,
        daily_limit=60,
        subscription=AsyncMock(return_value=None),
        counts=0,
        bus=HandlerBus(db),
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
    app.dependency_overrides[get_describe_log_guard] = lambda: DescribeLogGuard(
        enabled=state.enabled,
        daily_limit=state.daily_limit,
        require_subscription=state.subscription,
        increment_counter=count,
    )
    state.client = TestClient(app, raise_server_exceptions=False)
    yield state
    reset_limiter_counters()


def _post(env, text="1 ly trà đá", key="key-1", **body):
    headers = {"Idempotency-Key": key} if key else {}
    return env.client.post(URL, json={"text": text, **body}, headers=headers)


def _saved(env):
    return env.db.state.entries


def _model_calls(env):
    return env.bus.generation.generate_meal_plan_async.await_count


# --- a finished sentence replays -------------------------------------------


def test_same_key_saves_once_and_replays_the_same_entries(env):
    first = _post(env)
    second = _post(env)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert [entry["drink_id"] for entry in first.json()["entries"]] == ["tea"]
    assert len(_saved(env)) == 1
    assert _model_calls(env) == 1


def test_replay_comes_before_premium_and_the_quota(env):
    env.daily_limit = 1
    assert _post(env).status_code == 200
    env.subscription = AsyncMock(side_effect=HTTPException(status_code=402))

    resend = _post(env)

    assert resend.status_code == 200
    assert [e["drink_id"] for e in resend.json()["entries"]] == ["tea"]
    env.subscription.assert_not_called()
    assert env.counts == 1
    assert _post(env, key="key-2").status_code == 402


def test_replay_comes_before_the_switch(env):
    first = _post(env)
    env.enabled = False

    resend = _post(env)

    assert resend.status_code == 200 and resend.json() == first.json()
    assert _post(env, key="key-2").json()["detail"]["error_code"] == "FEATURE_DISABLED"


def test_same_key_with_another_sentence_is_a_conflict_before_the_guard(env):
    _post(env)
    env.subscription.reset_mock()

    response = _post(env, text="2 ly trà đá")

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "IDEMPOTENCY_KEY_CONFLICT"
    env.subscription.assert_not_called()
    assert len(_saved(env)) == 1


# --- a lost reply never saves twice; nothing saved runs fresh --------------


def test_commit_that_applied_but_raised_replays_on_retry(env):
    env.db.fail_write_commit = "lost"

    first = _post(env)
    retry = _post(env)

    assert first.status_code == 503  # a connection error after COMMIT
    assert retry.status_code == 200
    assert [e["drink_id"] for e in retry.json()["entries"]] == ["tea"]
    assert len(_saved(env)) == 1
    assert _model_calls(env) == 1


def test_commit_that_failed_runs_fresh_on_retry(env):
    env.db.fail_write_commit = "rolled_back"

    first = _post(env)
    retry = _post(env)

    assert first.status_code == 503
    assert _saved(env)[0].drink_id == "tea" and len(_saved(env)) == 1
    assert retry.status_code == 200
    assert _model_calls(env) == 2


def test_failed_sentence_frees_its_key_so_a_retry_runs(env):
    env.bus.generation.generate_meal_plan_async.return_value = {"items": []}

    first = _post(env)
    env.bus.generation.generate_meal_plan_async.return_value = {
        "items": [{"catalog_id": "water", "name": "nước"}]
    }
    second = _post(env)

    assert first.status_code == 422
    assert first.json()["detail"]["error_code"] == "DESCRIBE_NOTHING_RECOGNIZED"
    assert second.status_code == 200
    assert len(_saved(env)) == 1


# --- 409 IN_PROGRESS only while a lease is live, and it clears itself -------


def test_unreleased_claim_gives_409_until_its_lease_ends(env):
    env.db.fail_write_commit = "rolled_back"
    env.db.fail_release = True
    assert _post(env).status_code == 503
    env.db.fail_release = False
    env.subscription.reset_mock()
    counted = env.counts

    stuck = _post(env)
    env.db.advance(describe_log_replay.MIN_LEASE_SECONDS + 1)
    later = _post(env)

    assert stuck.status_code == 409
    assert stuck.json()["detail"]["error_code"] == "IDEMPOTENCY_KEY_IN_PROGRESS"
    assert later.status_code == 200
    assert len(_saved(env)) == 1
    env.subscription.assert_awaited_once()  # only the run after the lease
    assert env.counts == counted + 1


def test_request_whose_lease_was_taken_over_saves_nothing(env):
    """A slow request that outlives its lease must not save beside the new owner."""
    db = env.db

    async def slow_model(**_):
        db.advance(describe_log_replay.MIN_LEASE_SECONDS + 1)
        async with db.uow() as uow:  # a retry takes the expired lease
            await uow.meal_write_operations.reserve(
                user_id=USER_ID,
                operation=describe_log_replay.HYDRATION_DESCRIBE_OPERATION,
                idempotency_key="key-1",
                request_fingerprint=db.claim("key-1").fingerprint,
            )
        return TEA

    env.bus.generation.generate_meal_plan_async.side_effect = slow_model

    response = _post(env)

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "IDEMPOTENCY_KEY_IN_PROGRESS"
    assert _saved(env) == []


# --- other errors ------------------------------------------------------------


def test_invalid_ai_reply_after_the_correction_gives_422(env):
    env.bus.generation.generate_meal_plan_async.return_value = {
        "items": [{"catalog_id": "tea"}]
    }

    response = _post(env)

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "AI_OUTPUT_INVALID"
    assert _model_calls(env) == 2
    assert env.db.claim("key-1").status == "aborted"


def test_ai_outage_gives_503_and_saves_nothing(env):
    env.bus.generation.generate_meal_plan_async.side_effect = ConnectionError("down")

    response = _post(env)

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "AI_UNAVAILABLE"
    assert _saved(env) == []


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
    assert _saved(env) == []


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
    assert _saved(env) == []


def test_limiter_runs_before_premium(env):
    env.subscription = AsyncMock(side_effect=HTTPException(status_code=402))

    codes = [_post(env, key=f"key-{n}").status_code for n in range(21)]

    assert codes == [402] * 20 + [429]
    assert env.subscription.await_count == 20


def test_date_after_tomorrow_gives_400_and_frees_the_key(env):
    too_late = (utc_now().date() + timedelta(days=2)).isoformat()

    response = _post(env, target_date=too_late)

    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "INVALID_DATE"
    assert _model_calls(env) == 0
    assert env.db.claim("key-1").status == "aborted"


def test_sentence_longer_than_300_characters_is_refused(env):
    assert _post(env, text="n" * 301).status_code == 422
