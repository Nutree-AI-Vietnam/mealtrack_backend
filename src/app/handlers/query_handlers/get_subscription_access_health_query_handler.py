"""Monitoring: provider markers on RevenueCat rows against alert thresholds."""

from src.app.events.base import EventHandler, handles
from src.app.queries.subscription_access import GetSubscriptionAccessHealthQuery
from src.app.services.subscription_access_health import (
    SubscriptionAccessHealthReport,
    alert_thresholds_from_env,
    evaluate_marker_health,
    marker_cutoffs,
)
from src.domain.ports.subscription_access_support_repository_port import (
    SubscriptionAccessSupportRepositoryPort,
)
from src.domain.utils.timezone_utils import utc_now


@handles(GetSubscriptionAccessHealthQuery)
class GetSubscriptionAccessHealthQueryHandler(
    EventHandler[GetSubscriptionAccessHealthQuery, SubscriptionAccessHealthReport]
):
    """One read-only query over the marked rows (a partial index), so the
    uptime monitor can poll it every 5 minutes (runbook rule SA-5)."""

    def __init__(self, repository: SubscriptionAccessSupportRepositoryPort):
        self.repository = repository

    async def handle(
        self, query: GetSubscriptionAccessHealthQuery
    ) -> SubscriptionAccessHealthReport:
        now = utc_now()
        thresholds = alert_thresholds_from_env()
        sync_cutoff, refund_cutoff = marker_cutoffs(thresholds, now)
        counts = await self.repository.marker_health(
            sync_required_before=sync_cutoff, refund_pending_before=refund_cutoff
        )
        return evaluate_marker_health(counts, thresholds, now)
