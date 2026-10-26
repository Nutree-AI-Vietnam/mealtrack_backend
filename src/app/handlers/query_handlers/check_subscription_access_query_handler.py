"""Access decision for server-side enforcement on paid routes."""

from collections.abc import Callable

from src.app.events.base import EventHandler, handles
from src.app.queries.subscription_access import CheckSubscriptionAccessQuery
from src.app.services.subscription_access_fallback import (
    record_legacy_access_fallback,
)
from src.domain.model.subscription_access import (
    SubscriptionAccessUnavailable,
    evaluate_subscription_access,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort


@handles(CheckSubscriptionAccessQuery)
class CheckSubscriptionAccessQueryHandler(
    EventHandler[CheckSubscriptionAccessQuery, bool]
):
    """The database policy decides; when it cannot (an unreconciled row and
    no other grant), the legacy row check decides instead of failing the
    request: a row that still says 'active' and has not expired grants.
    """

    def __init__(self, uow_factory: Callable[[], AsyncUnitOfWorkPort]):
        self.uow_factory = uow_factory

    async def handle(self, query: CheckSubscriptionAccessQuery) -> bool:
        async with self.uow_factory() as uow:
            subscriptions = await uow.subscriptions.find_by_user_id(query.user_id)
            try:
                return evaluate_subscription_access(subscriptions).has_access
            except SubscriptionAccessUnavailable:
                record_legacy_access_fallback(
                    source="enforcement", user_id=query.user_id
                )
                return any(row.is_active() for row in subscriptions)
