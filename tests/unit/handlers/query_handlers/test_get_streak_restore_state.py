"""GET /v1/meals/streak: the restore state and its revision-fenced cache."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.app.handlers.query_handlers.get_streak_query_handler import (
    GetStreakQueryHandler,
)
from src.app.queries.meal.get_streak_query import GetStreakQuery
from src.app.services.streak_restore_state import (
    load_restore_state,
    purge_streak_cache,
    streak_cache_ttl,
    streak_payload,
)
from src.domain.cache.cache_keys import CacheKeys
from src.domain.model.streak import LedgerSummary, StreakRestorePolicy

ZONE = "Asia/Ho_Chi_Minh"


def _today() -> date:
    return datetime.now(ZoneInfo(ZONE)).date()


def _uow(log_days, *, balance=0, free_used=False, restored=()):
    return SimpleNamespace(
        users=SimpleNamespace(get_user_timezone=AsyncMock(return_value=ZONE)),
        meals=SimpleNamespace(
            get_streak_log_days=AsyncMock(return_value=log_days),
            count_by_source=AsyncMock(return_value=4),
        ),
        streak_restores=SimpleNamespace(
            summary=AsyncMock(
                return_value=LedgerSummary(balance, free_used, frozenset(restored))
            )
        ),
    )


def _twelve_then_a_miss():
    today = _today()
    return [(today - timedelta(days=offset), True) for offset in range(2, 14)]


@pytest.mark.asyncio
async def test_payload_carries_the_restore_state():
    today = _today()
    state = await load_restore_state(
        _uow(_twelve_then_a_miss(), balance=2), "u1", None, StreakRestorePolicy()
    )
    payload = streak_payload(state, StreakRestorePolicy())
    missed = today - timedelta(days=1)
    assert payload == {
        "current_streak": 0,
        "best_streak": 12,
        "last_logged_date": (today - timedelta(days=2)).isoformat(),
        "scan_count": 4,
        "restore": {
            "balance": 2,
            "free_available": True,
            "missed_dates": [missed.isoformat()],
            "restorable_until": (missed + timedelta(days=2)).isoformat(),
            "broken_streak": 12,
            "restored_dates": [],
        },
    }


@pytest.mark.asyncio
async def test_spending_switched_off_offers_nothing():
    policy = StreakRestorePolicy(spend_enabled=False)
    state = await load_restore_state(
        _uow(_twelve_then_a_miss(), free_used=True), "u1", None, policy
    )
    restore = streak_payload(state, policy)["restore"]
    assert restore["missed_dates"] == []
    assert restore["restorable_until"] is None
    assert restore["free_available"] is False


def _cache(cached, revision):
    cache = MagicMock()
    cache.get_json_with_revision = AsyncMock(return_value=(cached, revision))
    cache.set_json = AsyncMock()
    return cache


def _handler(cache):
    handler = GetStreakQueryHandler(cache_service=cache)
    uow = _uow(_twelve_then_a_miss())
    return handler, uow


async def _handle(handler, uow):
    unit = MagicMock()
    unit.__aenter__ = AsyncMock(return_value=uow)
    unit.__aexit__ = AsyncMock(return_value=None)
    with patch(
        "src.app.handlers.query_handlers.get_streak_query_handler.AsyncUnitOfWork",
        return_value=unit,
    ):
        return await handler.handle(GetStreakQuery(user_id="u1"))


@pytest.mark.asyncio
async def test_a_cache_hit_at_the_live_revision_is_served():
    cached = {"current_streak": 5, "best_streak": 9, "intake_revision": 7}
    cache = _cache(cached, 7)
    handler, uow = _handler(cache)
    result = await _handle(handler, uow)
    assert result == {"current_streak": 5, "best_streak": 9}
    uow.meals.get_streak_log_days.assert_not_awaited()
    cache.set_json.assert_not_awaited()
    assert cache.get_json_with_revision.call_args.args == (
        CacheKeys.user_streak("u1")[0],
        CacheKeys.intake_revision("u1")[0],
    )


@pytest.mark.asyncio
async def test_a_revision_mismatch_recomputes_and_stores_at_the_live_revision():
    cache = _cache({"current_streak": 5, "intake_revision": 6}, 7)
    handler, uow = _handler(cache)
    result = await _handle(handler, uow)
    assert result["restore"]["broken_streak"] == 12
    cache.set_json.assert_awaited_once()
    key, value, ttl = cache.set_json.call_args.args
    assert key == CacheKeys.user_streak("u1")[0]
    assert value == {**result, "intake_revision": 7}
    assert 60 <= ttl <= 3600
    assert cache.set_json.call_args.kwargs == {"revision_field": "intake_revision"}


@pytest.mark.asyncio
async def test_an_unknown_revision_never_serves_or_writes_the_cache():
    cache = _cache({"current_streak": 5, "intake_revision": None}, None)
    handler, uow = _handler(cache)
    result = await _handle(handler, uow)
    assert result["best_streak"] == 12
    cache.set_json.assert_not_awaited()


@pytest.mark.parametrize(
    ("local_now", "expected"),
    [
        (datetime(2026, 10, 28, 9, 0), 3600),
        (datetime(2026, 10, 28, 23, 30), 1800),
        (datetime(2026, 10, 28, 23, 59, 30), 30),
    ],
)
def test_ttl_is_capped_at_local_midnight(local_now, expected):
    now = local_now.replace(tzinfo=ZoneInfo(ZONE)).astimezone(UTC)
    assert streak_cache_ttl(ZONE, now) == expected


def test_ttl_counts_real_seconds_across_a_dst_change():
    # Clocks fall back at 02:00 on 2026-11-01 in Los Angeles, so 23:30 on
    # 2026-10-31 is 30 minutes from midnight whatever happens after it.
    zone = "America/Los_Angeles"
    now = datetime(2026, 10, 31, 23, 30, tzinfo=ZoneInfo(zone)).astimezone(UTC)
    assert streak_cache_ttl(zone, now) == 1800


@pytest.mark.asyncio
async def test_a_payload_computed_just_before_midnight_is_not_cached():
    cache = _cache(None, 7)
    handler, uow = _handler(cache)
    just_before = datetime(2026, 10, 28, 23, 59, 30, tzinfo=ZoneInfo(ZONE)).astimezone(
        UTC
    )
    with patch(
        "src.app.services.streak_restore_state.utc_now", return_value=just_before
    ):
        await _handle(handler, uow)
    cache.set_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_drops_the_streak_and_bumps_the_revision():
    cache = MagicMock(purge=AsyncMock())
    await purge_streak_cache(cache, "u1")
    revision_key, revision_ttl = CacheKeys.intake_revision("u1")
    cache.purge.assert_awaited_once_with(
        [CacheKeys.user_streak("u1")[0]], {revision_key: revision_ttl}
    )


@pytest.mark.asyncio
async def test_a_slow_purge_stops_blocking_but_still_finishes():
    finished = asyncio.Event()

    async def slow_purge(keys, revisions):
        await asyncio.sleep(0.2)
        finished.set()

    cache = MagicMock(purge=slow_purge)
    with patch("src.app.services.streak_restore_state._PURGE_TIMEOUT_SECONDS", 0.01):
        await purge_streak_cache(cache, "u1")
    assert not finished.is_set()
    await asyncio.wait_for(finished.wait(), timeout=1)


@pytest.mark.asyncio
async def test_a_failed_purge_does_not_fail_the_caller():
    cache = MagicMock(purge=AsyncMock(side_effect=ConnectionError("down")))
    await purge_streak_cache(cache, "u1")
