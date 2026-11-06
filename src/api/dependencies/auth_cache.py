"""Helpers for caching Firebase UID to active user ID lookups."""

from __future__ import annotations

import logging

from cachetools import TTLCache

from src.domain.utils.request_user_timezone import register_user_timezone_changed

logger = logging.getLogger(__name__)

# Short-lived (60s) in-process cache. Avoids a Redis round-trip on every
# authenticated request while keeping the stale-identity window small: the
# eviction in invalidate_cached_user_id only reaches the local worker, so other
# workers must expire by TTL after a user is deactivated/deleted.
# maxsize=15_000 covers 10K+ active users with headroom.
#
# Timezone rides on the same snapshot. It is the column from the auth SELECT,
# not a second cache. A committed timezone write updates every local entry for
# that user id; other workers still drop the snapshot when the 60s TTL ends.
_uid_cache: TTLCache[str, dict] = TTLCache(maxsize=15_000, ttl=60)


def _active_entry(firebase_uid: str) -> dict | None:
    entry = _uid_cache.get(firebase_uid)
    if not isinstance(entry, dict) or entry.get("is_active") is not True:
        return None
    return entry


async def get_cached_user_id(
    cache_service,  # kept for signature compatibility, no longer used
    firebase_uid: str,
) -> str | None:
    """Return cached database user ID only when the cached user is active."""
    entry = _active_entry(firebase_uid)
    if entry is None:
        return None
    user_id = entry.get("user_id")
    return str(user_id) if user_id else None


def cached_user_timezone(firebase_uid: str) -> str | None:
    """Return the timezone stored with the active auth snapshot, if any."""
    entry = _active_entry(firebase_uid)
    if entry is None:
        return None
    timezone = entry.get("timezone")
    return timezone if isinstance(timezone, str) else None


async def set_cached_user_id(
    cache_service,  # kept for signature compatibility, no longer used
    firebase_uid: str,
    user_id: str,
    is_active: bool,
    timezone: str | None = None,
) -> None:
    """Cache database user identity for the Firebase UID."""
    entry: dict = {"user_id": str(user_id), "is_active": bool(is_active)}
    if isinstance(timezone, str):
        entry["timezone"] = timezone
    _uid_cache[firebase_uid] = entry


async def invalidate_cached_user_id(
    cache_service,  # kept for signature compatibility, no longer used
    firebase_uid: str,
) -> None:
    """Invalidate cached Firebase UID mapping."""
    _uid_cache.pop(firebase_uid, None)


def _apply_committed_timezone(user_id: str, timezone: str) -> None:
    """Refresh every local auth snapshot for this user after a committed write."""
    target = str(user_id)
    for entry in _uid_cache.values():
        if isinstance(entry, dict) and str(entry.get("user_id")) == target:
            entry["timezone"] = timezone


register_user_timezone_changed(_apply_committed_timezone)
