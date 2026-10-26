"""Database-only subscription access query handler."""

from collections.abc import Callable
from typing import Any

from src.app.events.base import EventHandler, handles
from src.app.queries.subscription_access import GetMyEntitlementsQuery
from src.domain.model.subscription_access import (
    SubscriptionAccess,
    evaluate_subscription_access,
    subscription_will_renew,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort


def entitlements_payload(access: SubscriptionAccess) -> dict[str, Any]:
    """The GET /v1/me/entitlements body for one access decision.

    product_id, will_renew and provider describe the granting row with the
    latest deadline (a lifetime grant wins); all null when access is denied.
    """
    source = access.source if access.has_access else None
    return {
        "has_access": access.has_access,
        "status": access.status,
        "expires_at": access.expires_at,
        "product_id": source.product_id if source is not None else None,
        "will_renew": subscription_will_renew(source) if source is not None else None,
        "provider": source.provider if source is not None else None,
    }


@handles(GetMyEntitlementsQuery)
class GetMyEntitlementsQueryHandler(EventHandler[GetMyEntitlementsQuery, dict]):
    def __init__(self, uow_factory: Callable[[], AsyncUnitOfWorkPort]):
        self.uow_factory = uow_factory

    async def handle(self, query: GetMyEntitlementsQuery) -> dict:
        """Raises SubscriptionAccessUnavailable when stored facts cannot decide."""
        async with self.uow_factory() as uow:
            subscriptions = await uow.subscriptions.find_by_user_id(query.user_id)
            return entitlements_payload(evaluate_subscription_access(subscriptions))
