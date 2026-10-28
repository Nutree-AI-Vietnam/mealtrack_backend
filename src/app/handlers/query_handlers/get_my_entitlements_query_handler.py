"""Database-only subscription access query handler."""

from collections.abc import Callable

from src.app.events.base import EventHandler, handles
from src.app.queries.subscription_access import GetMyEntitlementsQuery
from src.domain.model.subscription_access import (
    entitlements_payload,
    evaluate_subscription_access,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort

__all__ = ["GetMyEntitlementsQueryHandler", "entitlements_payload"]


@handles(GetMyEntitlementsQuery)
class GetMyEntitlementsQueryHandler(EventHandler[GetMyEntitlementsQuery, dict]):
    def __init__(self, uow_factory: Callable[[], AsyncUnitOfWorkPort]):
        self.uow_factory = uow_factory

    async def handle(self, query: GetMyEntitlementsQuery) -> dict:
        """Raises SubscriptionAccessUnavailable when stored facts cannot decide."""
        async with self.uow_factory() as uow:
            subscriptions = await uow.subscriptions.find_by_user_id(query.user_id)
            return entitlements_payload(evaluate_subscription_access(subscriptions))
