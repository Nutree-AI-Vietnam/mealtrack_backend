"""Adopt fully resolved provider search hits into the durable food catalog."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def is_adoptable_provider_hit(item: dict[str, Any]) -> bool:
    if item.get("source") != "fatsecret":
        return False
    if not (item.get("source_food_id") or item.get("food_id")):
        return False
    # Search-description macros lack metric_serving_amount; only adopt
    # fully resolved food.get.v5 (or servings-embedded) hits.
    if item.get("metric_serving_amount") is None:
        return False
    return all(
        item.get(field) is not None
        for field in ("protein_100g", "carbs_100g", "fat_100g")
    )


async def adopt_provider_hits(
    items: list[dict[str, Any]], *, locale: str, uow_factory: Any | None
) -> None:
    """Adopt fully-resolved FatSecret hits and tag them with their catalog id.

    Only hits with a durable ``source_food_id`` and valid per-100g macros are
    adopted; thin/autocomplete stubs must be filtered by the caller before this
    runs (never called for autocomplete).
    """
    if uow_factory is None:
        return
    adoptable = [item for item in items if is_adoptable_provider_hit(item)]
    if not adoptable:
        return
    try:
        async with uow_factory() as uow:
            identities = [
                (
                    str(item.get("source_namespace") or "fatsecret"),
                    str(item.get("source_food_id") or item.get("food_id") or ""),
                )
                for item in adoptable
            ]
            existing_rows = await uow.food_references.get_by_source_identities(
                identities
            )
            existing_by_key = {
                (
                    str(row.get("source_namespace") or ""),
                    str(row.get("source_food_id") or ""),
                ): row
                for row in existing_rows
            }
            for item in adoptable:
                namespace = str(item.get("source_namespace") or "fatsecret")
                food_id = str(item.get("source_food_id") or item.get("food_id") or "")
                existing = existing_by_key.get((namespace, food_id))
                if existing and existing.get("id") is not None:
                    item["food_reference_id"] = existing.get("id")
                    continue
                display_name = str(item.get("description") or item.get("name") or "")
                english_name = str(item.get("canonical_name") or display_name)
                try:
                    adopted = await adopt_one_provider_hit(
                        uow,
                        item,
                        english_name=english_name,
                        locale=locale,
                        display_name=display_name,
                    )
                except Exception:
                    logger.warning("food search adopt failed", exc_info=True)
                    continue
                item["food_reference_id"] = adopted.get("id")
    except Exception:
        logger.warning("food search adopt failed", exc_info=True)


async def adopt_one_provider_hit(
    uow: Any,
    item: dict[str, Any],
    *,
    english_name: str,
    locale: str,
    display_name: str,
) -> dict[str, Any]:
    """Adopt one hit, rolling back only that write if it fails.

    A shared UoW session is left unusable after an unhandled DB error.
    A savepoint isolates one IntegrityError (or similar) so later hits
    can still adopt.
    """

    async def _call() -> dict[str, Any]:
        return await uow.food_references.adopt_provider_food(
            item.get("source_namespace") or "fatsecret",
            str(item.get("source_food_id") or item.get("food_id")),
            english_name,
            {
                "protein_100g": item.get("protein_100g"),
                "carbs_100g": item.get("carbs_100g"),
                "fat_100g": item.get("fat_100g"),
                "fiber_100g": item.get("fiber_100g") or 0,
                "sugar_100g": item.get("sugar_100g") or 0,
            },
            item.get("allowed_units"),
            locale,
            display_name,
        )

    session = getattr(uow, "session", None)
    begin_nested = getattr(session, "begin_nested", None)
    if begin_nested is None:
        return await _call()
    async with begin_nested():
        return await _call()
