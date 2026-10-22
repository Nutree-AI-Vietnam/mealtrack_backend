"""Database-only subscription access query handler."""

from src.app.events.base import EventHandler, handles
from src.app.queries.subscription_access import GetMyEntitlementsQuery
from src.domain.services.subscription_access_policy import (
    evaluate_subscription_access,
)
from src.infra.database.uow_async import AsyncUnitOfWork


@handles(GetMyEntitlementsQuery)
class GetMyEntitlementsQueryHandler(EventHandler[GetMyEntitlementsQuery, dict]):
    async def handle(self, query: GetMyEntitlementsQuery) -> dict:
        async with AsyncUnitOfWork() as uow:
            subscriptions = await uow.subscriptions.find_by_user_id(query.user_id)
            access = evaluate_subscription_access(subscriptions)
            return {
                "has_access": access.has_access,
                "status": access.status,
                "expires_at": access.expires_at,
            }
