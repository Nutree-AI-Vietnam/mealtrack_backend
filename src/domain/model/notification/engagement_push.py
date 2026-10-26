"""Server-sent engagement pushes, queued in the ``notifications`` outbox.

The device plans almost every notification in the catalog itself. The few the
server sends (today only "payment failed") wait in the outbox until the
user's next allowed local window, so nobody is woken at night.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.domain.constants.languages import resolve_app_locale
from src.domain.utils.timezone_utils import DEFAULT_TIMEZONE, get_zone_info


class EngagementPushKind(StrEnum):
    BILLING_ISSUE = "billing_issue"


class EngagementPushStatus(StrEnum):
    PENDING = "pending"
    # Claimed by a dispatcher. A crash leaves the row here until it expires,
    # so a push can be lost but is never sent twice.
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"
    EXPIRED = "expired"


# Pushes whose time the server picks go out between 09:00 and 20:00 local.
SEND_WINDOW_START = time(9, 0)
SEND_WINDOW_END = time(20, 0)

# A payment reminder that arrives two days late is no longer worth sending.
BILLING_ISSUE_PUSH_TTL = timedelta(hours=48)

# Screen the app opens when the push is tapped.
ENGAGEMENT_PUSH_ROUTES: Mapping[EngagementPushKind, str] = MappingProxyType(
    {EngagementPushKind.BILLING_ISSUE: "/profile"}
)


@dataclass(frozen=True, slots=True)
class NewEngagementPush:
    """A push to queue. The outbox keeps one per user, kind and local date."""

    id: str
    user_id: str
    kind: EngagementPushKind
    # The user's local date at ``scheduled_for_utc``.
    scheduled_date: date
    scheduled_for_utc: datetime
    expires_at: datetime
    # Render snapshot; FCM targets are looked up when the push is sent.
    context: dict[str, str]


@dataclass(frozen=True, slots=True)
class EngagementPushClaim:
    """A due push reserved for a single delivery attempt."""

    id: str
    user_id: str
    kind: EngagementPushKind
    context: dict[str, Any]


def next_send_time(now: datetime, timezone: str | None) -> datetime:
    """Return when a push may go out, in UTC.

    That is ``now`` while it is 09:00–20:00 in the user's zone, otherwise the
    next 09:00 there. A missing or unknown zone counts as UTC.
    """
    return _next_send_time(_as_utc(now), _zone(timezone))


def plan_billing_issue_push(
    *,
    user_id: str,
    timezone: str | None,
    language: str | None,
    now: datetime,
) -> NewEngagementPush:
    """The payment-failed push, due in the user's next allowed window."""
    zone = _zone(timezone)
    send_at = _next_send_time(_as_utc(now), zone)
    kind = EngagementPushKind.BILLING_ISSUE
    return NewEngagementPush(
        id=str(uuid4()),
        user_id=user_id,
        kind=kind,
        scheduled_date=send_at.astimezone(zone).date(),
        scheduled_for_utc=send_at,
        expires_at=send_at + BILLING_ISSUE_PUSH_TTL,
        context={"kind": kind.value, "language": resolve_app_locale(language)},
    )


def _next_send_time(now_utc: datetime, zone: ZoneInfo) -> datetime:
    local = now_utc.astimezone(zone)
    if SEND_WINDOW_START <= local.time() < SEND_WINDOW_END:
        return now_utc
    day = local.date()
    if local.time() >= SEND_WINDOW_END:
        day += timedelta(days=1)
    # Built from the local wall clock, so the UTC offset is the one in force
    # on that day even when a DST change falls in between.
    return datetime.combine(day, SEND_WINDOW_START, tzinfo=zone).astimezone(UTC)


def _zone(timezone: str | None) -> ZoneInfo:
    return get_zone_info(timezone or DEFAULT_TIMEZONE)


def _as_utc(moment: datetime) -> datetime:
    # Naive values are taken to be UTC, as everywhere else in the backend.
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
