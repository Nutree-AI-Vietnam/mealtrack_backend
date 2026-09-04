"""Handler for bulk activities query — returns activities grouped by date."""

import logging
from typing import Any

from src.app.events.base import EventHandler, handles
from src.app.queries.activity.get_bulk_activities_query import GetBulkActivitiesQuery
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.meal_projection import MealProjection
from src.domain.ports.cache_port import CachePort
from src.domain.services.hydration_catalog_service import (
    localized_name_for_catalog_name,
)
from src.domain.services.meal_calorie_service import effective_meal_calories
from src.domain.services.meal_nrf_fields import (
    hydration_entry_nrf_fields,
    meal_nrf_fields,
)
from src.domain.utils.timezone_utils import (
    format_iso_utc,
    get_zone_info,
    resolve_user_timezone_async,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = {MealStatus.READY, MealStatus.ENRICHING}


def _build_meal_activity(meal: Meal, language: str = "en") -> dict[str, Any]:
    title = meal.dish_name or "Unknown Meal"
    if language and language != "en" and meal.translations:
        translation = meal.translations.get(language)
        if translation and translation.dish_name:
            title = translation.dish_name

    image_url = meal.image.url if meal.image else None
    return {
        "id": meal.meal_id,
        "type": "meal",
        "timestamp": format_iso_utc(meal.created_at),
        "title": title,
        "emoji": meal.emoji,
        "meal_type": meal.meal_type,
        "calories": round(effective_meal_calories(meal), 1),
        "macros": {
            "protein": round(meal.nutrition.macros.protein, 1) if meal.nutrition else 0,
            "carbs": round(meal.nutrition.macros.carbs, 1) if meal.nutrition else 0,
            "fat": round(meal.nutrition.macros.fat, 1) if meal.nutrition else 0,
        },
        "quantity": meal.quantity,
        "status": meal.status.value if meal.status else "unknown",
        "image_url": image_url,
        "source": meal.source,
        **meal_nrf_fields(meal),
    }


def _build_hydration_activity(meal: Meal, language: str = "en") -> dict[str, Any]:
    kcal = round(meal.nutrition.calories, 1) if meal.nutrition else 0
    macros = {
        "protein": round(meal.nutrition.macros.protein, 1) if meal.nutrition else 0,
        "carbs": round(meal.nutrition.macros.carbs, 1) if meal.nutrition else 0,
        "fat": round(meal.nutrition.macros.fat, 1) if meal.nutrition else 0,
    }
    return {
        "id": meal.meal_id,
        "type": "hydration",
        "timestamp": format_iso_utc(meal.created_at),
        "title": localized_name_for_catalog_name(meal.dish_name, language) or "Water",
        "emoji": meal.emoji or "💧",
        "meal_type": "hydration",
        "calories": kcal,
        "macros": macros,
        "quantity": meal.quantity or 0,
        "volume_ml": meal.quantity or 0,
        "status": "completed",
        "image_url": None,
        "source": meal.source or "hydration",
        **meal_nrf_fields(meal),
        "meal_id": meal.meal_id,
    }


def _build_hydration_entry_activity(entry, language: str = "en") -> dict[str, Any]:
    return {
        "id": entry.id,
        "type": "hydration",
        "timestamp": format_iso_utc(entry.logged_at),
        "title": localized_name_for_catalog_name(
            entry.drink_name_snapshot,
            language,
            drink_id=entry.drink_id,
        )
        or entry.drink_name_snapshot
        or "Water",
        "drink_id": entry.drink_id,
        "emoji": entry.emoji_snapshot or "💧",
        "meal_type": "hydration",
        "calories": round(entry.calories, 1),
        "macros": {
            "protein": round(entry.protein_g or 0, 1),
            "carbs": round(entry.carbs_g or 0, 1),
            "fat": round(entry.fat_g or 0, 1),
        },
        "quantity": entry.credited_ml,
        "volume_ml": entry.volume_ml,
        "status": "completed",
        "image_url": entry.image_url,
        "source": entry.source,
        **hydration_entry_nrf_fields(entry),
        "meal_id": entry.legacy_meal_id,
    }


@handles(GetBulkActivitiesQuery)
class GetBulkActivitiesQueryHandler(
    EventHandler[GetBulkActivitiesQuery, dict[str, list[dict[str, Any]]]]
):
    def __init__(self, cache_service: CachePort | None = None):
        self.cache_service = cache_service

    async def handle(
        self, query: GetBulkActivitiesQuery
    ) -> dict[str, list[dict[str, Any]]]:
        async with AsyncUnitOfWork() as uow:
            user_tz_str = await resolve_user_timezone_async(
                query.user_id, uow, query.header_timezone
            )
            tz = get_zone_info(user_tz_str)

            meals = await uow.meals.find_by_date_range(
                user_id=query.user_id,
                start_date=query.start_date,
                end_date=query.end_date,
                user_timezone=user_tz_str,
                projection=MealProjection.FULL_WITH_TRANSLATIONS,
            )
            hydration_entries = await uow.hydration_entries.find_by_date_range(
                user_id=query.user_id,
                start_date=query.start_date,
                end_date=query.end_date,
                user_timezone=user_tz_str,
            )

        language = query.language or "en"
        result: dict[str, list[dict[str, Any]]] = {}
        covered_meal_ids = {
            entry.legacy_meal_id for entry in hydration_entries if entry.legacy_meal_id
        }

        for meal in meals:
            try:
                if meal.status not in _ACTIVE_STATUSES:
                    continue
                if not meal.created_at:
                    continue
                local_date = meal.created_at.astimezone(tz).date().isoformat()
                if local_date not in result:
                    result[local_date] = []
                if meal.meal_type == "hydration":
                    if meal.meal_id in covered_meal_ids:
                        continue
                    result[local_date].append(_build_hydration_activity(meal, language))
                else:
                    result[local_date].append(_build_meal_activity(meal, language))
            except Exception:
                logger.exception(
                    "Skipping bulk meal activity for meal_id=%s",
                    getattr(meal, "meal_id", None),
                )

        for entry in hydration_entries:
            try:
                if not entry.logged_at:
                    continue
                local_date = entry.logged_at.astimezone(tz).date().isoformat()
                if local_date not in result:
                    result[local_date] = []
                result[local_date].append(
                    _build_hydration_entry_activity(entry, language)
                )
            except Exception:
                logger.exception(
                    "Skipping bulk hydration entry activity for entry_id=%s",
                    getattr(entry, "id", None),
                )

        return result
