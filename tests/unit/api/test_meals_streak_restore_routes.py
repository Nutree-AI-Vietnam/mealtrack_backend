"""POST /v1/meals/streak/restore: auth, request checks and error statuses."""

from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exception_handlers import register_exception_handlers
from src.api.exceptions import (
    AuthorizationException,
    BusinessLogicException,
    ConflictException,
    ExternalServiceException,
    ResourceNotFoundException,
)
from src.api.middleware.rate_limit import reset_limiter_counters
from src.api.routes.v1.meals_streak_restore import router
from src.app.commands.streak import RestoreStreakCommand
from src.app.handlers.command_handlers.restore_streak_command_handler import (
    RestoreStreakCommandHandler,
)
from src.domain.model.streak import StreakRestorePolicy

STREAK = {"current_streak": 12, "best_streak": 12, "scan_count": 0}
OK = {"outcome": "restored", "restored_dates": ["2026-10-27"], "credited": 0}


class _Bus:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.sent = reply, error, []

    async def send(self, message):
        self.sent.append(message)
        if self.error:
            raise self.error
        return self.reply


def _app(bus, signed_in=True):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/v1/meals")
    if signed_in:
        app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    return app


async def _post(app, body, headers=None):
    reset_limiter_counters()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        return await api.post("/v1/meals/streak/restore", json=body, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [None, {"Authorization": "Bearer not-a-token"}])
async def test_requests_without_a_valid_token_are_refused(headers):
    bus = _Bus(reply={**OK, "streak": STREAK})
    response = await _post(
        _app(bus, signed_in=False), {"dates": ["2026-10-27"]}, headers
    )
    assert response.status_code == 401
    assert bus.sent == []


@pytest.mark.asyncio
async def test_a_restore_reaches_the_handler_with_the_callers_id_and_zone():
    bus = _Bus(reply={**OK, "streak": STREAK})
    response = await _post(
        _app(bus),
        {
            "dates": ["2026-10-27"],
            "purchase": {
                "product_id": "nutree_streak_restore_1",
                "transaction_id": "t",
            },
        },
        {"X-Timezone": "Asia/Ho_Chi_Minh"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "restored"
    command = bus.sent[0]
    assert isinstance(command, RestoreStreakCommand)
    assert command.user_id == "user-1"
    assert command.header_timezone == "Asia/Ho_Chi_Minh"
    assert command.purchase.transaction_id == "t"
    assert [day.isoformat() for day in command.dates] == ["2026-10-27"]


def _validating_bus():
    handler = RestoreStreakCommandHandler(
        uow_factory=MagicMock(side_effect=AssertionError("no database")),
        cache_service=None,
        subscription_service=MagicMock(),
        policy=StreakRestorePolicy(),
    )

    class _HandlerBus:
        async def send(self, message):
            return await handler.handle(message)

    return _HandlerBus()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"dates": ["2026-10-25", "2026-10-26", "2026-10-27"]},
        {"dates": ["2026-10-27", "2026-10-26"]},
        {"dates": []},
        {},
    ],
)
async def test_bad_days_are_a_400(body):
    response = await _post(_app(_validating_bus()), body)
    assert response.status_code == 400, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status"),
    [
        (AuthorizationException("x", "SANDBOX_PURCHASE_NOT_ALLOWED"), 403),
        (ConflictException("x", "PURCHASE_OWNED_BY_ANOTHER_ACCOUNT"), 409),
        (ConflictException("x", "STREAK_NOT_RESTORABLE"), 409),
        (ConflictException("x", "NO_RESTORES_LEFT"), 409),
        (BusinessLogicException("x", "UNKNOWN_RESTORE_PRODUCT"), 422),
        (ResourceNotFoundException("x", "PURCHASE_NOT_FOUND"), 404),
        (ExternalServiceException("x", "PURCHASE_VERIFICATION_UNAVAILABLE"), 503),
        (ExternalServiceException("x", "FEATURE_DISABLED"), 503),
    ],
)
async def test_errors_keep_their_status_and_code(error, status):
    response = await _post(_app(_Bus(error=error)), {"dates": ["2026-10-27"]})
    assert response.status_code == status
    assert response.json()["detail"]["error_code"] == error.error_code


def test_the_meals_router_mounts_the_restore_route():
    from src.api.routes.v1.meals import router as meals_router

    routes = {
        (route.path, method)
        for route in meals_router.routes
        for method in route.methods
    }
    assert ("/v1/meals/streak/restore", "POST") in routes
