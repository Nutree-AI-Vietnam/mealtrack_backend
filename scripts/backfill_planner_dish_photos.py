"""Photograph AI-written plan dishes that were stored without a photo.

Weeks written before planner dishes were photographed show every dish on
the empty plate. This draws each such dish the way the week writer now does
(PlannerDishPhotos over the chat meal photographer, so a dish already drawn
is reused rather than drawn again) and stores the URL on its slot.

No database transaction is open while photos are made. Each slot is then
written on its own, and only while it still holds the same dish with no
photo, so a dish the user swapped meanwhile is left alone. Neither the
plan's revision nor the slot's version moves: a photo is not an edit the
app has to reconcile.

    python scripts/backfill_planner_dish_photos.py --dry-run
    python scripts/backfill_planner_dish_photos.py --plan-id <plan id>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import lazyload

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.app.services.planner_dish_photos import PlannerDishPhotos
from src.domain.model.weekly_meal_planner.saved_recipe import SavedRecipeSnapshot
from src.domain.services.weekly_meal_planner.ai_dish import SOURCE_PLANNER_AI
from src.infra.database.models.weekly_meal_planner.weekly_meal_plan_slot import (
    WeeklyMealPlanSlotORM,
)
from src.infra.database.uow_async import AsyncUnitOfWork

# Every photo starts at once here, with no week being written alongside, so
# a run waits for all of them rather than for a week's last few stragglers.
SETTLE_SECONDS = 600.0


def main() -> None:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(
        description="Photograph AI-written plan dishes stored without a photo."
    )
    parser.add_argument(
        "--plan-id", action="append", default=[], help="Only this plan (repeatable)."
    )
    parser.add_argument(
        "--limit", type=int, default=210, help="Most dishes to photograph in one run."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="List the dishes; draw and store none."
    )
    args = parser.parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    summary = asyncio.run(_run(args))
    for key, value in summary.items():
        print(f"{key}={value}")
    if summary["failed"]:
        raise SystemExit(1)


async def _run(args, *, photographer=None) -> dict[str, int]:
    async with AsyncUnitOfWork() as uow:
        if uow.session is None:
            raise RuntimeError("AsyncUnitOfWork did not initialize a database session")
        dishes = await _load_dishes(
            uow.session, plan_ids=tuple(args.plan_id), limit=args.limit
        )
    for slot_id, dish in dishes.items():
        print(f"slot={slot_id} dish={dish.english_name or dish.name}")
    if args.dry_run or not dishes:
        return {"selected": len(dishes), "updated": 0, "skipped": 0, "failed": 0}

    if photographer is None:
        from src.api.base_dependencies import get_chat_meal_image_generator

        photographer = get_chat_meal_image_generator()
    if photographer is None:
        raise SystemExit(
            "Cloudflare is not configured: set CLOUDFLARE_ACCOUNT_ID and "
            "CLOUDFLARE_API_TOKEN"
        )

    photos = PlannerDishPhotos(
        photographer, plan_id="backfill", settle_seconds=SETTLE_SECONDS
    )
    for dish in dishes.values():
        photos.start(dish)
    drawn = await photos.attach(dishes)

    updated = skipped = failed = 0
    for slot_id, dish in drawn.items():
        if not dish.image_url:
            failed += 1
            print(f"photo_failed slot={slot_id}", file=sys.stderr)
        elif await _store(slot_id, dish):
            updated += 1
        else:
            skipped += 1
            print(f"skipped slot={slot_id}: it changed meanwhile", file=sys.stderr)
    return {
        "selected": len(dishes),
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
    }


async def _load_dishes(
    session, *, plan_ids: tuple[str, ...], limit: int
) -> dict[str, SavedRecipeSnapshot]:
    """AI-written dishes with no photo, by slot id, oldest first."""
    stmt = (
        select(WeeklyMealPlanSlotORM)
        .options(lazyload(WeeklyMealPlanSlotORM.catalog_meal))
        .where(
            WeeklyMealPlanSlotORM.saved_recipe["source"].as_string()
            == SOURCE_PLANNER_AI
        )
        .order_by(WeeklyMealPlanSlotORM.created_at, WeeklyMealPlanSlotORM.id)
    )
    if plan_ids:
        stmt = stmt.where(WeeklyMealPlanSlotORM.plan_id.in_(plan_ids))
    dishes: dict[str, SavedRecipeSnapshot] = {}
    for row in (await session.execute(stmt)).scalars():
        dish = SavedRecipeSnapshot.from_dict(
            row.saved_recipe, suggestion_id=row.saved_suggestion_id
        )
        if dish.image_url:
            continue
        dishes[str(row.id)] = dish
        if len(dishes) >= limit:
            break
    return dishes


async def _store(slot_id: str, dish: SavedRecipeSnapshot) -> bool:
    """Write the photo while the slot still holds this dish with none."""
    async with AsyncUnitOfWork() as uow:
        if uow.session is None:
            raise RuntimeError("AsyncUnitOfWork did not initialize a database session")
        row = (
            await uow.session.execute(
                select(WeeklyMealPlanSlotORM)
                .options(lazyload(WeeklyMealPlanSlotORM.catalog_meal))
                .where(WeeklyMealPlanSlotORM.id == slot_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not _still_unphotographed(row, dish):
            return False
        # A new dict, so the JSON column is seen as changed.
        row.saved_recipe = {**row.saved_recipe, "image_url": dish.image_url}
        await uow.session.flush()
        return True


def _still_unphotographed(row, dish: SavedRecipeSnapshot) -> bool:
    if row is None or row.saved_suggestion_id != dish.suggestion_id:
        return False
    stored = row.saved_recipe if isinstance(row.saved_recipe, dict) else {}
    return not str(stored.get("image_url") or "").strip()


if __name__ == "__main__":
    main()
