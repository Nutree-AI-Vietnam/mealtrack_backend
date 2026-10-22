"""Database-only subscription access query handler."""

from collections.abc import Callable

from src.app.events.base import EventHandler, handles
from src.app.queries.subscription_access import GetMyEntitlementsQuery
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.services.subscription_access_policy import (
    evaluate_subscription_access,
)


@handles(GetMyEntitlementsQuery)
class GetMyEntitlementsQueryHandler(EventHandler[GetMyEntitlementsQuery, dict]):
    def __init__(self, uow_factory: Callable[[], AsyncUnitOfWorkPort]):
        self.uow_factory = uow_factory

    async def handle(self, query: GetMyEntitlementsQuery) -> dict:
        async with self.uow_factory() as uow:
            subscriptions = await uow.subscriptions.find_by_user_id(query.user_id)
            access = evaluate_subscription_access(subscriptions)
            return {
                "has_access": access.has_access,
                "status": access.status,
                "expires_at": access.expires_at,
            }
