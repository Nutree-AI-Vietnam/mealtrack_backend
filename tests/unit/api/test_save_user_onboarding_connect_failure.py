"""Onboarding save stays retryable when the DB connect attempts are exhausted.

``POST /v1/user-profiles/`` sends ``SaveUserOnboardingCommand``. A connect
failure surfaces as ``TimeoutError`` (asyncpg's ``compat.timeout``). The route
converts that through ``handle_exception`` before the client sees it.
"""

import pytest
from fastapi import HTTPException

from src.api.routes.v1.user_profiles import save_user_onboarding
from src.api.schemas.request.onboarding_requests import OnboardingCompleteRequest
from src.app.commands.user import SaveUserOnboardingCommand


class _ConnectFailedBus:
    def __init__(self) -> None:
        self.commands: list[object] = []

    async def send(self, message: object) -> None:
        self.commands.append(message)
        raise TimeoutError("handshake stalled")


def _request() -> OnboardingCompleteRequest:
    return OnboardingCompleteRequest(
        birth_year=1990,
        birth_month=1,
        birth_day=15,
        gender="female",
        height=170,
        weight=65,
        job_type="desk",
        training_days_per_week=3,
        training_minutes_per_session=45,
        goal="cut",
    )


@pytest.mark.asyncio
async def test_onboarding_save_returns_503_when_connect_fails():
    bus = _ConnectFailedBus()

    with pytest.raises(HTTPException) as exc_info:
        await save_user_onboarding(_request(), user_id="user-1", event_bus=bus)

    assert len(bus.commands) == 1
    assert isinstance(bus.commands[0], SaveUserOnboardingCommand)
    error = exc_info.value
    assert error.status_code == 503
    assert error.detail["error_code"] == "AI_UNAVAILABLE"
    assert error.detail["details"] == {}
    assert "handshake stalled" not in str(error.detail)
