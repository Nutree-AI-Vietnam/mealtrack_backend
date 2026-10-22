"""Contract tests for the single mobile subscription-access endpoint."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response

from src.api.routes.v1.me import get_my_entitlements
from src.app.queries.subscription_access import GetMyEntitlementsQuery


@pytest.mark.asyncio
async def test_entitlements_uses_authenticated_user_and_private_no_store(monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", "true")

    class EventBus:
        async def send(self, query):
            assert query == GetMyEntitlementsQuery("db-user-id")
            return {
                "has_access": True,
                "status": "active",
                "expires_at": datetime(2026, 11, 22, tzinfo=UTC),
            }

    response = Response()
    result = await get_my_entitlements(
        response, user_id="db-user-id", event_bus=EventBus()
    )
    assert result.has_access is True
    assert result.status == "active"
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_entitlements_stays_unavailable_while_feature_flag_is_off(monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", "false")
    event_bus = AsyncMock()
    response = Response()
    with pytest.raises(HTTPException) as error:
        await get_my_entitlements(response, user_id="db-user-id", event_bus=event_bus)
    assert error.value.status_code == 503
    assert error.value.detail == {"error_code": "ENTITLEMENTS_UNAVAILABLE"}
    assert response.headers["cache-control"] == "private, no-store"
    event_bus.send.assert_not_awaited()
