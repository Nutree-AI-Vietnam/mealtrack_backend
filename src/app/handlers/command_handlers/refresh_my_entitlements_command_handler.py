"""Refresh the signed-in user's access rows from their provider customer."""

from src.app.commands.subscription_access import RefreshMyEntitlementsCommand
from src.app.events.base import EventHandler, handles
from src.domain.ports.subscription_access_refresh_port import (
    SubscriptionAccessRefresh,
    SubscriptionAccessRefreshPort,
)


@handles(RefreshMyEntitlementsCommand)
class RefreshMyEntitlementsCommandHandler(
    EventHandler[RefreshMyEntitlementsCommand, SubscriptionAccessRefresh]
):
    """Runs whatever SUBSCRIPTION_ACCESS_ENABLED says: a refresh only writes
    verified provider facts, which also helps reconciliation while it is off.
    """

    def __init__(self, access_refresher: SubscriptionAccessRefreshPort):
        self.access_refresher = access_refresher

    async def handle(
        self, command: RefreshMyEntitlementsCommand
    ) -> SubscriptionAccessRefresh:
        """Raises RevenueCatSnapshotUnavailable when the customer is unreadable."""
        return await self.access_refresher.refresh(command.user_id)
