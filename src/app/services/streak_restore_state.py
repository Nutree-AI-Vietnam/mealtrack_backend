"""The streak with its restore state, as GET /v1/meals/streak returns it."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from src.app.services.supplement_days import with_dose_days
from src.domain.cache.cache_keys import CacheKeys
from src.domain.model.streak import (
    StreakCalendar,
    StreakRestorePolicy,
    build_streak_calendar,
)
from src.domain.ports.cache_port import CachePort
from src.domain.utils.timezone_utils import (
    get_zone_info,
    resolve_user_timezone_async,
    utc_now,
)

logger = logging.getLogger(__name__)

# Shorter than this before local midnight, the payload is not cached at all.
MIN_CACHE_TTL = 60
# A slow Redis must not add its socket timeout to a restore or webhook.
_PURGE_TIMEOUT_SECONDS = 1.0
# Strong references so a purge nobody waits for any more is not collected.
_purges: set[asyncio.Future[None]] = set()


@dataclass(frozen=True)
class RestoreState:
    timezone: str
    computed_at: datetime  # the instant ``today`` was read at
    today: date
    calendar: StreakCalendar
    balance: int
    free_available: bool
    restored_dates: frozenset[date]  # every day this user restored
    scan_count: int


async def load_restore_state(
    uow: Any,  # AsyncUnitOfWorkPort; the meal reads are not on its meal port
    user_id: str,
    header_timezone: str | None,
    policy: StreakRestorePolicy,
) -> RestoreState:
    """Read logs and the ledger in the caller's unit of work."""
    user_tz = await resolve_user_timezone_async(user_id, uow, header_timezone)
    computed_at = utc_now()
    today = computed_at.astimezone(get_zone_info(user_tz)).date()
    # A supplement dose counts as a logged day, like water.
    log_days = await with_dose_days(
        uow,
        user_id,
        await uow.meals.get_streak_log_days(user_id, user_timezone=user_tz),
    )
    ledger = await uow.streak_restores.summary(user_id)
    scan_count = await uow.meals.count_by_source(user_id, "scanner")
    return RestoreState(
        timezone=user_tz,
        computed_at=computed_at,
        today=today,
        calendar=build_streak_calendar(
            log_days, ledger.restored_dates, today, policy.same_day_logs_from
        ),
        balance=ledger.balance,
        free_available=not ledger.free_used,
        restored_dates=ledger.restored_dates,
        scan_count=scan_count,
    )


def streak_payload(state: RestoreState, policy: StreakRestorePolicy) -> dict[str, Any]:
    calendar = state.calendar
    # With spending switched off nothing is offered; the rest still shows.
    offer = calendar.offer if policy.spend_enabled else None
    last_logged = calendar.last_logged_date
    return {
        "current_streak": calendar.current_streak,
        "best_streak": calendar.best_streak,
        "last_logged_date": last_logged.isoformat() if last_logged else None,
        "scan_count": state.scan_count,
        "restore": {
            "balance": state.balance,
            "free_available": state.free_available,
            "missed_dates": [day.isoformat() for day in offer.missed_dates]
            if offer
            else [],
            "restorable_until": offer.restorable_until.isoformat() if offer else None,
            "broken_streak": offer.broken_streak if offer else 0,
            "restored_dates": [day.isoformat() for day in calendar.restored_recent],
        },
    }


def streak_cache_ttl(user_timezone: str, computed_at: datetime) -> int:
    """Seconds the streak may stay cached: at most an hour, never past local midnight.

    Counted from when the state was computed, so a computation that straddles
    midnight gets 0 and is not cached.
    """
    local_now = computed_at.astimezone(get_zone_info(user_timezone))
    midnight = datetime.combine(
        local_now.date() + timedelta(days=1), time.min, tzinfo=local_now.tzinfo
    )
    # Subtract in UTC: same-zone arithmetic ignores a DST change in between.
    until_midnight = midnight.astimezone(UTC) - local_now.astimezone(UTC)
    return max(
        0, min(CacheKeys.user_streak("")[1], int(until_midnight.total_seconds()))
    )


async def purge_streak_cache(cache: CachePort | None, user_id: str) -> None:
    """Drop the cached streak and fence any read already in flight.

    Waits at most a second; a slower purge is not cancelled and finishes in
    the background, so the stale entry still goes once Redis answers.
    """
    if cache is None:
        return
    revision_key, revision_ttl = CacheKeys.intake_revision(user_id)
    purge = asyncio.ensure_future(
        cache.purge([CacheKeys.user_streak(user_id)[0]], {revision_key: revision_ttl})
    )
    _purges.add(purge)
    purge.add_done_callback(_purges.discard)
    try:
        await asyncio.wait_for(asyncio.shield(purge), timeout=_PURGE_TIMEOUT_SECONDS)
    except TimeoutError:
        purge.add_done_callback(_log_late_failure)
        logger.warning("streak_restore_cache_purge_slow user_id=%s", user_id)
    except Exception:
        logger.warning(
            "streak_restore_cache_purge_failed user_id=%s", user_id, exc_info=True
        )


def _log_late_failure(purge: asyncio.Future[None]) -> None:
    if not purge.cancelled() and purge.exception() is not None:
        logger.warning(
            "streak_restore_cache_purge_failed late", exc_info=purge.exception()
        )
