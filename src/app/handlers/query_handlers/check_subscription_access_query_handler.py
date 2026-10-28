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
    fallback_may_grant,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort


@handles(CheckSubscriptionAccessQuery)
class CheckSubscriptionAccessQueryHandler(
    EventHandler[CheckSubscriptionAccessQuery, bool]
):
    """The database policy decides; when it cannot (an unreconciled row and
    no other grant), a fallback decides instead of failing the request.

    The fallback is never stricter than the policy: any row whose status can
    grant ('active', 'cancelled' paid through, 'billing_issue') and that is
    unexpired or in its grace period grants. A webhook whose RevenueCat read
    failed leaves exactly such rows behind (a CANCELLATION writes
    'cancelled', a BILLING_ISSUE 'billing_issue'), and their payers keep the
    paid routes until a snapshot resolves the marker. 'expired' and
    'refunded' rows never grant, and neither does a row a refund marked
    while its RevenueCat read failed (it also reads 'cancelled'): the
    refund may have ended its paid period.
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
                return any(fallback_may_grant(row) for row in subscriptions)
