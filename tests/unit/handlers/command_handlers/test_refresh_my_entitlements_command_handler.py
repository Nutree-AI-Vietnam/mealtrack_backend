"""RefreshMyEntitlementsCommandHandler delegates to the refresh port."""

from unittest.mock import AsyncMock

import pytest

from src.app.commands.subscription_access import RefreshMyEntitlementsCommand
from src.app.handlers.command_handlers.refresh_my_entitlements_command_handler import (
    RefreshMyEntitlementsCommandHandler,
)
from src.domain.ports.subscription_access_refresh_port import (
    SubscriptionAccessRefresh,
)
from src.domain.ports.subscription_service_port import RevenueCatSnapshotUnavailable


@pytest.mark.asyncio
async def test_refresh_runs_for_the_command_user(monkeypatch):
    # Runs whatever the flag says: the route decides how to answer.
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", "false")
    refresher = AsyncMock()
    refresher.refresh.return_value = SubscriptionAccessRefresh("applied")

    result = await RefreshMyEntitlementsCommandHandler(refresher).handle(
        RefreshMyEntitlementsCommand("db-user-id")
    )

    assert result == SubscriptionAccessRefresh("applied")
    refresher.refresh.assert_awaited_once_with("db-user-id")


@pytest.mark.asyncio
async def test_unreadable_customer_propagates_to_the_route():
    refresher = AsyncMock()
    refresher.refresh.side_effect = RevenueCatSnapshotUnavailable(
        "down", retry_after_seconds=3, reason="http_5xx"
    )

    with pytest.raises(RevenueCatSnapshotUnavailable):
        await RefreshMyEntitlementsCommandHandler(refresher).handle(
            RefreshMyEntitlementsCommand("db-user-id")
        )
