"""Thresholds and verdict of GET /v1/health/subscription-access.

The uptime monitor polls the endpoint and alerts on any non-200. Markers are
the provider markers a failed webhook read leaves on RevenueCat rows (sync
required, refund pending, transfer source); with no scheduled repair, they
clear only when a snapshot taken after them is applied (the user's next
webhook or refresh, a support resync, or a reconcile run), so a growing or
ageing backlog is what pages. Ages are measured from RevenueCat's event time.

Thresholds come from the environment, read at request time (invalid values
fall back to the default, logged):

| Variable | Default | Degraded when |
|---|---|---|
| SUBSCRIPTION_ACCESS_ALERT_SYNC_REQUIRED_MINUTES | 30 | (with the next) |
| SUBSCRIPTION_ACCESS_ALERT_SYNC_REQUIRED_USERS | 20 | more users than this have a sync-required marker older than the minutes above |
| SUBSCRIPTION_ACCESS_ALERT_REFUND_PENDING_MINUTES | 60 | any refund-pending marker is older |
| SUBSCRIPTION_ACCESS_ALERT_OLDEST_MARKER_HOURS | 6 | the oldest marker of any type is older |
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.domain.ports.subscription_access_support_repository_port import (
    AccessMarkerHealth,
)

logger = logging.getLogger(__name__)

HEALTH_OK = "ok"
HEALTH_DEGRADED = "degraded"

REASON_SYNC_REQUIRED_BACKLOG = "sync_required_backlog"
REASON_REFUND_PENDING_STALE = "refund_pending_stale"
REASON_OLDEST_MARKER_STALE = "oldest_marker_stale"

ENV_SYNC_REQUIRED_MINUTES = "SUBSCRIPTION_ACCESS_ALERT_SYNC_REQUIRED_MINUTES"
ENV_SYNC_REQUIRED_USERS = "SUBSCRIPTION_ACCESS_ALERT_SYNC_REQUIRED_USERS"
ENV_REFUND_PENDING_MINUTES = "SUBSCRIPTION_ACCESS_ALERT_REFUND_PENDING_MINUTES"
ENV_OLDEST_MARKER_HOURS = "SUBSCRIPTION_ACCESS_ALERT_OLDEST_MARKER_HOURS"


@dataclass(frozen=True)
class AccessAlertThresholds:
    sync_required_minutes: float = 30
    sync_required_users: int = 20
    refund_pending_minutes: float = 60
    oldest_marker_hours: float = 6


def _positive_number(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        logger.warning("Ignoring %s=%r: not a positive number", name, raw)
        return default
    return value


def _non_negative_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        logger.warning("Ignoring %s=%r: not a whole number >= 0", name, raw)
        return default
    return value


def alert_thresholds_from_env() -> AccessAlertThresholds:
    defaults = AccessAlertThresholds()
    return AccessAlertThresholds(
        sync_required_minutes=_positive_number(
            ENV_SYNC_REQUIRED_MINUTES, defaults.sync_required_minutes
        ),
        sync_required_users=_non_negative_int(
            ENV_SYNC_REQUIRED_USERS, defaults.sync_required_users
        ),
        refund_pending_minutes=_positive_number(
            ENV_REFUND_PENDING_MINUTES, defaults.refund_pending_minutes
        ),
        oldest_marker_hours=_positive_number(
            ENV_OLDEST_MARKER_HOURS, defaults.oldest_marker_hours
        ),
    )


def marker_cutoffs(
    thresholds: AccessAlertThresholds, now: datetime
) -> tuple[datetime, datetime]:
    """(sync-required cutoff, refund-pending cutoff): older markers are stale."""
    return (
        now - timedelta(minutes=thresholds.sync_required_minutes),
        now - timedelta(minutes=thresholds.refund_pending_minutes),
    )


@dataclass(frozen=True)
class HealthReason:
    code: str
    message: str


@dataclass(frozen=True)
class SubscriptionAccessHealthReport:
    status: str  # ok | degraded
    checked_at: datetime
    counts: AccessMarkerHealth
    oldest_marker_age_seconds: int | None
    thresholds: AccessAlertThresholds
    reasons: list[HealthReason] = field(default_factory=list)


def _minutes(value: float) -> str:
    return f"{value:g} minutes"


def evaluate_marker_health(
    counts: AccessMarkerHealth, thresholds: AccessAlertThresholds, now: datetime
) -> SubscriptionAccessHealthReport:
    """Degraded when any threshold is exceeded, with one reason per breach."""
    oldest_age = (
        max(0, int((now - counts.oldest_marker_at).total_seconds()))
        if counts.oldest_marker_at is not None
        else None
    )
    reasons: list[HealthReason] = []
    if counts.stale_sync_required_users > thresholds.sync_required_users:
        reasons.append(
            HealthReason(
                REASON_SYNC_REQUIRED_BACKLOG,
                f"{counts.stale_sync_required_users} users have a sync-required "
                f"marker older than {_minutes(thresholds.sync_required_minutes)} "
                f"(alert above {thresholds.sync_required_users})",
            )
        )
    if counts.stale_refund_pending_rows > 0:
        reasons.append(
            HealthReason(
                REASON_REFUND_PENDING_STALE,
                f"{counts.stale_refund_pending_rows} refund-pending rows are older "
                f"than {_minutes(thresholds.refund_pending_minutes)}",
            )
        )
    if oldest_age is not None and oldest_age > thresholds.oldest_marker_hours * 3600:
        reasons.append(
            HealthReason(
                REASON_OLDEST_MARKER_STALE,
                f"the oldest marker is {oldest_age // 60} minutes old "
                f"(alert above {thresholds.oldest_marker_hours:g} hours)",
            )
        )
    return SubscriptionAccessHealthReport(
        status=HEALTH_DEGRADED if reasons else HEALTH_OK,
        checked_at=now,
        counts=counts,
        oldest_marker_age_seconds=oldest_age,
        thresholds=thresholds,
        reasons=reasons,
    )
