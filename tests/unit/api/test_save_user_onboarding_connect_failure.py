"""Onboarding save stays retryable when the DB connect attempts are exhausted.

``POST /v1/user-profiles/`` sends ``SaveUserOnboardingCommand``. Two failed
connects (the pooler retry in ``connect_with_one_retry``) must surface as
503 ``DB_UNAVAILABLE``, not as an AI outage. The route converts the error
through ``handle_exception`` before the client sees it.
"""

import pytest
from fastapi import HTTPException

from src.api.routes.v1.user_profiles import save_user_onboarding
from src.api.schemas.request.onboarding_requests import OnboardingCompleteRequest
from src.app.commands.user import SaveUserOnboardingCommand
from src.infra.database.config_async import connect_with_one_retry


class _ConnectFailedBus:
    def __init__(self, error: BaseException) -> None:
        self.commands: list[object] = []
        self._error = error

    async def send(self, message: object) -> None:
        self.commands.append(message)
        raise self._error


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
async def test_onboarding_save_after_two_failed_connects_returns_db_unavailable():
    attempts = 0

    async def connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise TimeoutError("handshake stalled")

    with pytest.raises(Exception) as connect_error:
        await connect_with_one_retry(connect)

    assert attempts == 2
    bus = _ConnectFailedBus(connect_error.value)

    with pytest.raises(HTTPException) as exc_info:
        await save_user_onboarding(_request(), user_id="user-1", event_bus=bus)

    assert len(bus.commands) == 1
    assert isinstance(bus.commands[0], SaveUserOnboardingCommand)
    error = exc_info.value
    assert error.status_code == 503
    assert error.detail["error_code"] == "DB_UNAVAILABLE"
    assert error.detail["message"] == (
        "The database is temporarily unavailable. Please try again."
    )
    assert error.detail["details"] == {}
    assert "handshake stalled" not in str(error.detail)
    assert "AI meal generation" not in str(error.detail)
