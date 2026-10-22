"""POST /v1/payments/momo/activate: who may unlock quiz passes, and no 500s."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from src.api.dependencies.auth import get_current_user_id, verify_firebase_token
from src.api.middleware.rate_limit import (
    limiter,
    rate_limit_exceeded_handler,
    reset_limiter_counters,
)
from src.api.routes.v1.momo_payments import router

EMAIL = "buyer@example.com"


class FakeMomoPasses:
    def __init__(self, holds_pass=True, error=None):
        self.holds_pass, self.error, self.calls = holds_pass, error, []

    async def activate(self, user_id, verified_email):
        self.calls.append((user_id, verified_email))
        if self.error:
            raise self.error
        return self.holds_pass


def _token(provider: str, verified: bool) -> dict:
    return {
        "uid": "firebase-uid-1",
        "email": EMAIL,
        "email_verified": verified,
        "firebase": {"sign_in_provider": provider},
    }


@pytest.fixture
def activate(monkeypatch):
    def call(passes, token, times=1):
        monkeypatch.setattr(
            "src.bootstrap.momo_payments.get_momo_pass_service", lambda: passes
        )
        app = FastAPI()
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
        app.include_router(router)
        app.dependency_overrides[get_current_user_id] = lambda: "user-1"
        app.dependency_overrides[verify_firebase_token] = lambda: token
        client = TestClient(app)
        return [client.post("/v1/payments/momo/activate") for _ in range(times)]

    reset_limiter_counters()
    yield call
    reset_limiter_counters()


@pytest.mark.parametrize(
    ("provider", "verified", "unlock_email"),
    [
        ("google.com", True, EMAIL),
        ("apple.com", True, EMAIL),
        ("password", True, EMAIL),
        ("password", False, None),
        ("custom", True, None),
    ],
    ids=["google", "apple", "password", "unverified", "custom-token"],
)
def test_only_a_proven_email_may_unlock_quiz_passes(
    activate, provider, verified, unlock_email
):
    passes = FakeMomoPasses()

    [response] = activate(passes, _token(provider, verified))

    assert response.status_code == 200
    assert response.json() == {"has_pass": True}
    assert passes.calls == [("user-1", unlock_email)]


def test_user_without_a_pass_gets_false(activate):
    [response] = activate(FakeMomoPasses(holds_pass=False), _token("google.com", True))

    assert response.status_code == 200
    assert response.json() == {"has_pass": False}


def test_failed_activation_answers_false_instead_of_an_error(activate):
    passes = FakeMomoPasses(error=RuntimeError("database is down"))

    [response] = activate(passes, _token("google.com", True))

    assert response.status_code == 200
    assert response.json() == {"has_pass": False}
    assert passes.calls == [("user-1", EMAIL)]


def test_activation_is_rate_limited(activate):
    passes = FakeMomoPasses()

    responses = activate(passes, _token("google.com", True), times=11)

    assert [response.status_code for response in responses] == [200] * 10 + [429]
    assert len(passes.calls) == 10
