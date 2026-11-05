"""Generate meal value insights in the API process.

`MealInsightEventHandler` handles meal integration events when
``INTEGRATION_EVENT_TRANSPORT=in_process``, in every environment.
`LocalMealInsightWriter` fills keys in local Docker Redis for development
while events still go to the Queue.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from src.app.events.meal.meal_insight_snapshot import insight_language_name
from src.domain.model.ai.model_purpose import ModelPurpose
from src.domain.services.meal_value_insight_contract import (
    parse_ai_result,
    serialize_insights,
)
from src.domain.services.meal_value_insight_service import MealValueInsightService

logger = logging.getLogger(__name__)

MEAL_INSIGHT_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
# The revision outlives the insight so a late write cannot revive a cleared one.
MEAL_INSIGHT_FENCE_TTL_SECONDS = 8 * 24 * 60 * 60
MEAL_DELETED_EVENT = "meal.deleted.v1"
_LOCAL_REDIS_HOSTS = {"localhost", "127.0.0.1", "::1", "mealtrack_redis"}


def redis_url_is_local(redis_url: str) -> bool:
    """Return True when Redis is the local Docker instance, not hosted Upstash."""
    host = (urlparse(redis_url).hostname or "").lower()
    return host in _LOCAL_REDIS_HOSTS


class LocalMealInsightWriter:
    """Generate Worker-shaped insights and write them to local Redis."""

    def __init__(
        self,
        cache_getter: Callable[[], Any],
        ai_getter: Callable[[], Any],
    ) -> None:
        self._cache_getter = cache_getter
        self._ai_getter = ai_getter

    def schedule(
        self,
        meal_id: str,
        insight: dict[str, Any],
        occurred_at: datetime,
    ) -> None:
        """Start background generation; never block the meal write response."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._write(meal_id, insight, occurred_at))
        task.add_done_callback(_log_background_failure)

    async def _write(
        self,
        meal_id: str,
        insight: dict[str, Any],
        occurred_at: datetime,
    ) -> None:
        cache = self._cache_getter()
        if cache is None or not getattr(cache, "enabled", True):
            return
        redis_url = getattr(getattr(cache, "redis", None), "_redis_url", "")
        if not redis_url_is_local(str(redis_url)):
            return
        try:
            raw = await self._ai_getter().generate(
                purpose=ModelPurpose.GENERAL,
                prompt=_build_prompt(insight),
                system_message=_system_prompt(insight.get("language")),
                response_type="json",
                max_tokens=1200,
            )
        except Exception:
            logger.info(
                "local meal insight generation failed meal_id=%s",
                meal_id,
                exc_info=True,
            )
            return
        parsed = parse_ai_result(raw)
        if parsed is None:
            logger.info("local meal insight payload invalid meal_id=%s", meal_id)
            return
        await cache.set_json(
            MealValueInsightService.cache_key_for_meal(meal_id),
            serialize_insights(parsed),
            MEAL_INSIGHT_CACHE_TTL_SECONDS,
        )
        logger.info(
            "local meal insight cached meal_id=%s occurred_at=%s",
            meal_id,
            occurred_at.isoformat(),
        )


class MealInsightEventHandler:
    """Turn a meal integration event into the meal's stored value insights.

    Every meal event first clears the stored insight and records the event's
    revision (its ``occurred_at``): an edit drops insights written for the old
    meal, and an older generation still in flight can no longer overwrite a
    newer one. Created and updated events then generate from the event's
    insight snapshot; a re-published event with the same revision regenerates.
    """

    def __init__(
        self,
        cache_getter: Callable[[], Any],
        ai_getter: Callable[[], Any],
    ) -> None:
        self._cache_getter = cache_getter
        self._ai_getter = ai_getter

    async def __call__(self, payload: dict[str, Any]) -> None:
        cache = self._cache_getter()
        if cache is None or not getattr(cache, "enabled", True):
            return
        data = payload.get("data") or {}
        meal_id = str(data.get("meal_id") or payload.get("aggregate_id") or "")
        revision = event_revision(payload.get("occurred_at"))
        if not meal_id or revision is None:
            logger.info("meal insight event skipped: missing meal id or occurred_at")
            return
        key = MealValueInsightService.cache_key_for_meal(meal_id)
        if not await cache.invalidate_with_revision(
            key, revision, MEAL_INSIGHT_FENCE_TTL_SECONDS
        ):
            return
        insight = data.get("insight")
        if payload.get("event_type") == MEAL_DELETED_EVENT or not isinstance(
            insight, dict
        ):
            return
        raw = await self._ai_getter().generate(
            purpose=ModelPurpose.GENERAL,
            prompt=_build_prompt(insight),
            system_message=_system_prompt(insight.get("language")),
            response_type="json",
            max_tokens=1200,
        )
        parsed = parse_ai_result(raw)
        if parsed is None:
            logger.info("meal insight payload invalid meal_id=%s", meal_id)
            return
        stored = await cache.set_json_with_revision(
            key,
            serialize_insights(parsed),
            revision,
            MEAL_INSIGHT_CACHE_TTL_SECONDS,
        )
        logger.info("meal insight generated meal_id=%s stored=%s", meal_id, stored)


def event_revision(occurred_at: Any) -> int | None:
    """Milliseconds since the epoch of an event's ``occurred_at``."""
    if isinstance(occurred_at, datetime):
        moment = occurred_at
    elif isinstance(occurred_at, str):
        try:
            moment = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000)


def _system_prompt(language: str | None) -> str:
    name = insight_language_name(language)
    return (
        "You are Nutree's nutrition insight writer. Return only valid JSON. "
        f"Write every meal_bullet.text and ingredient_insights.text in {name}. "
        "JSON keys stay English. Keep logged dish and ingredient names unchanged. "
        "Give practical food guidance, not medical diagnosis. Avoid disease claims, "
        "treatment claims, and unsupported certainty."
    )


def _build_prompt(insight: dict[str, Any]) -> str:
    language = insight.get("language")
    name = insight_language_name(language)
    nutrition = insight.get("nutrition") or {}
    summary = {
        "dish_name": insight.get("dish_name"),
        "language": language,
        "output_language": name,
        "macros": {
            key: nutrition.get(key)
            for key in (
                "calories",
                "protein_g",
                "carbs_g",
                "fat_g",
                "fiber_g",
                "sugar_g",
            )
        },
        "micros": nutrition.get("micros") or {},
        "ingredients": (insight.get("ingredients") or [])[:8],
        "user_context": insight.get("user_context") or {},
    }
    has_micros = bool(summary["micros"])
    micro_rule = (
        "- Analyze both macros and micros. One meal_bullet must cite a macro "
        "(protein, carbs, fat, fiber, sugar, or calories). The other must cite a "
        "logged micronutrient (iron, sodium, potassium, calcium, etc.).\n"
        if has_micros
        else "- Micros are absent; analyze macros only.\n"
    )
    return (
        "Generate concise meal value insights from this logged meal.\n"
        "Rules:\n"
        f"- Write ALL meal_bullet.text and ingredient_insights.text in {name}. "
        "Do not write English sentences unless the output language is English.\n"
        "- JSON keys stay English. Keep logged dish and ingredient names unchanged.\n"
        "- meal_bullets: max 2 items, each text <=120 characters.\n"
        "- ingredient_insights: max 2 key ingredients, one line each, text <=120 characters.\n"
        f"{micro_rule}"
        "- Ingredient lines should use that item's macros or micros when they "
        "explain the body effect.\n"
        "- Micros are portion totals. Typical units: vitamin_a mcg; "
        "vitamin_c/e mg; minerals mg; saturated_fat and added_sugar g.\n"
        "- category must be benefit, caution, or balance.\n"
        "- For each item, set highlights to exactly 1 exact substring from text worth spotlighting.\n\n"
        'JSON shape:\n{"meal_bullets":[{"text":"...","category":"benefit","highlights":["..."]}],'
        '"ingredient_insights":[{"ingredient_name":"...","text":"...","category":"balance",'
        '"highlights":["..."]}]}\n\n'
        f"Meal:\n{summary}"
    )


def _log_background_failure(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.info("local meal insight task failed error=%s", type(error).__name__)
