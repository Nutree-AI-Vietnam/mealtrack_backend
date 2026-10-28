"""Support's repair queue: users whose RevenueCat rows carry a marker."""

from src.app.events.base import EventHandler, handles
from src.app.queries.subscription_access import ListUnresolvedSubscriptionAccessQuery
from src.app.services.subscription_access_support import UnresolvedQueuePage
from src.domain.ports.subscription_access_support_repository_port import (
    SubscriptionAccessSupportRepositoryPort,
)
from src.domain.utils.timezone_utils import utc_now


@handles(ListUnresolvedSubscriptionAccessQuery)
class ListUnresolvedSubscriptionAccessQueryHandler(
    EventHandler[ListUnresolvedSubscriptionAccessQuery, UnresolvedQueuePage]
):
    def __init__(self, repository: SubscriptionAccessSupportRepositoryPort):
        self.repository = repository

    async def handle(
        self, query: ListUnresolvedSubscriptionAccessQuery
    ) -> UnresolvedQueuePage:
        # One row past the page says whether another page follows.
        users = await self.repository.list_unresolved(
            limit=query.limit + 1, after=query.after
        )
        page = users[: query.limit]
        more = len(users) > query.limit
        return UnresolvedQueuePage(
            items=page,
            next_cursor=page[-1].cursor if more and page else None,
            checked_at=utc_now(),
        )
