"""Support: one user's access rows, the policy's decision, and optionally a
read-only look at their RevenueCat customer."""

from src.app.events.base import EventHandler, handles
from src.app.queries.subscription_access import GetSupportSubscriptionAccessQuery
from src.app.services.subscription_access_support import (
    LIVE_NOT_READ,
    LIVE_OK,
    LIVE_UNAVAILABLE,
    MAX_LOOKUP_MATCHES,
    SUPPORT_LOOKUP_SOURCE,
    LiveRead,
    SupportAccessView,
    SupportUserAmbiguous,
    SupportUserNotFound,
    current_access_flags,
    describe_access_decision,
    not_live_reason,
)
from src.domain.ports.subscription_access_refresh_port import (
    SubscriptionAccessRefreshPort,
)
from src.domain.ports.subscription_access_support_repository_port import (
    SubscriptionAccessSupportRepositoryPort,
    SupportUserRecord,
)
from src.domain.ports.subscription_service_port import RevenueCatSnapshotUnavailable


@handles(GetSupportSubscriptionAccessQuery)
class GetSupportSubscriptionAccessQueryHandler(
    EventHandler[GetSupportSubscriptionAccessQuery, SupportAccessView]
):
    """Reads the database only, unless ``live``: RevenueCat's GET
    /v1/subscribers is get-or-create, so a plain lookup never calls it."""

    def __init__(
        self,
        repository: SubscriptionAccessSupportRepositoryPort,
        access_refresher: SubscriptionAccessRefreshPort,
    ):
        self.repository = repository
        self.access_refresher = access_refresher

    async def handle(
        self, query: GetSupportSubscriptionAccessQuery
    ) -> SupportAccessView:
        """Raises SupportUserNotFound or SupportUserAmbiguous."""
        matches = await self.repository.find_users(
            query.query, limit=MAX_LOOKUP_MATCHES
        )
        if not matches:
            raise SupportUserNotFound("No user matches the query")
        if len(matches) > 1:
            raise SupportUserAmbiguous([user.id for user in matches])
        user = matches[0]
        rows = await self.repository.subscription_rows(user.id)
        return SupportAccessView(
            user=user,
            subscriptions=rows,
            decision=describe_access_decision(rows),
            flags=current_access_flags(),
            live=await self._live_read(user) if query.live else None,
        )

    async def _live_read(self, user: SupportUserRecord) -> LiveRead:
        """RevenueCat's view, never applied: a failure is reported, not raised,
        so support still sees the stored rows while RevenueCat is down."""
        reason = not_live_reason(user)
        if reason is not None:
            return LiveRead(LIVE_NOT_READ, reason=reason)
        try:
            peek = await self.access_refresher.peek(
                user.id, source=SUPPORT_LOOKUP_SOURCE
            )
        except RevenueCatSnapshotUnavailable as error:
            return LiveRead(
                LIVE_UNAVAILABLE,
                reason=getattr(error, "reason", None),
                retry_after_seconds=getattr(error, "retry_after_seconds", None),
            )
        return LiveRead(LIVE_OK, peek=peek)
