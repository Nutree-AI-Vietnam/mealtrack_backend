"""Handle the backend's integration events in the API process.

These handlers replaced the retired nutreeai_async worker and Cloudflare Queue:

- ``meal.created/updated/deleted.v1``: meal value insights.
- ``user.deleted.v1`` / ``user.account_cleanup.v1``: Firebase account deletion.
- Profile, onboarding and custom-macro events: the user's target caches.
- ``cheat_day.*``: that week's budget caches.
- ``saved_suggestion.*``: the saved suggestions list.

Meal, hydration and movement day caches are purged on the write path before
the response, so those events need nothing more here. Subscription receipts
and meal recommendation plans had no reader and are not carried over.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from src.api.services.meal_insight_event_handler import MealInsightEventHandler
from src.api.services.write_cache_invalidation import (
    apply_cheat_day_cache_invalidation,
    apply_profile_cache_invalidation,
    apply_saved_suggestion_cache_invalidation,
)
from src.app.events.in_process_event_router import (
    InProcessIntegrationEventRouter,
    in_process_event_router,
)

logger = logging.getLogger(__name__)

MEAL_EVENTS = ("meal.created.v1", "meal.updated.v1", "meal.deleted.v1")
ACCOUNT_CLEANUP_EVENTS = ("user.deleted.v1", "user.account_cleanup.v1")
PROFILE_EVENTS = (
    "user.profile_updated.v1",
    "user.onboarding_completed.v1",
    "user.custom_macros_updated.v1",
)
CHEAT_DAY_EVENTS = ("cheat_day.marked.v1", "cheat_day.unmarked.v1")
SAVED_SUGGESTION_EVENTS = (
    "saved_suggestion.created.v1",
    "saved_suggestion.deleted.v1",
)


def register_in_process_event_handlers(
    *,
    cache_getter: Callable[[], Any],
    ai_getter: Callable[[], Any],
    delete_firebase_account: Callable[[str], Awaitable[None]],
    router: InProcessIntegrationEventRouter = in_process_event_router,
) -> None:
    """Replace the router's handlers with this process's own."""
    router.clear()
    router.register(MEAL_EVENTS, MealInsightEventHandler(cache_getter, ai_getter))

    async def delete_account(payload: dict[str, Any]) -> None:
        firebase_uid = _data(payload).get("firebase_uid")
        if not firebase_uid:
            logger.warning(
                "account cleanup event has no firebase_uid event_id=%s",
                payload.get("event_id"),
            )
            return
        await delete_firebase_account(str(firebase_uid))

    async def purge_profile(payload: dict[str, Any]) -> None:
        user_id = _data(payload).get("user_id") or payload.get("aggregate_id")
        await apply_profile_cache_invalidation(cache_getter(), str(user_id or ""))

    async def purge_cheat_day(payload: dict[str, Any]) -> None:
        data = _data(payload)
        cheat_day = _parse_date(data.get("date"))
        if cheat_day is None:
            return
        await apply_cheat_day_cache_invalidation(
            cache_getter(), str(data.get("user_id") or ""), cheat_day
        )

    async def purge_saved_suggestions(payload: dict[str, Any]) -> None:
        await apply_saved_suggestion_cache_invalidation(
            cache_getter(), str(_data(payload).get("user_id") or "")
        )

    router.register(ACCOUNT_CLEANUP_EVENTS, delete_account)
    router.register(PROFILE_EVENTS, purge_profile)
    router.register(CHEAT_DAY_EVENTS, purge_cheat_day)
    router.register(SAVED_SUGGESTION_EVENTS, purge_saved_suggestions)


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
