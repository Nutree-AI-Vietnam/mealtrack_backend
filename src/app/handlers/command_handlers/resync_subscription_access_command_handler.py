"""Support: resync one user's access rows from their RevenueCat customer."""

from src.app.commands.subscription_access import ResyncSubscriptionAccessCommand
from src.app.events.base import EventHandler, handles
from src.app.services.subscription_access_support import (
    SUPPORT_RESYNC_SOURCE,
    SupportResyncOutcome,
    SupportUserNotFound,
    SupportUserNotLive,
    describe_access_decision,
    not_live_reason,
)
from src.domain.ports.subscription_access_refresh_port import (
    SubscriptionAccessRefreshPort,
)
from src.domain.ports.subscription_access_support_repository_port import (
    SubscriptionAccessSupportRepositoryPort,
)


@handles(ResyncSubscriptionAccessCommand)
class ResyncSubscriptionAccessCommandHandler(
    EventHandler[ResyncSubscriptionAccessCommand, SupportResyncOutcome]
):
    """The app's own refresh, for a user support names.

    The refresher reads the customer outside any transaction, applies it
    with sync_revenuecat_access in one unit of work (user row locked,
    markers the snapshot answers cleared) and commits. A user the rows
    remember as the source of a TRANSFER has an empty customer applied as
    authoritative, exactly as reconcile --repair does: the sync itself
    honours provider_transfer_source_at.
    """

    def __init__(
        self,
        repository: SubscriptionAccessSupportRepositoryPort,
        access_refresher: SubscriptionAccessRefreshPort,
    ):
        self.repository = repository
        self.access_refresher = access_refresher

    async def handle(
        self, command: ResyncSubscriptionAccessCommand
    ) -> SupportResyncOutcome:
        """Raises SupportUserNotFound, SupportUserNotLive,
        RevenueCatSnapshotUnavailable (the customer could not be read) or
        SubscriptionAccessApplyError (already reported)."""
        user = await self.repository.get_user(command.user_id)
        if user is None:
            raise SupportUserNotFound("No user has this id")
        reason = not_live_reason(user)
        if reason is not None:
            raise SupportUserNotLive(reason)
        before = describe_access_decision(
            await self.repository.subscription_rows(user.id)
        )
        sync = await self.access_refresher.refresh(
            user.id, source=SUPPORT_RESYNC_SOURCE
        )
        after = describe_access_decision(
            await self.repository.subscription_rows(user.id)
        )
        return SupportResyncOutcome(
            user_id=user.id, before=before, after=after, sync=sync
        )
