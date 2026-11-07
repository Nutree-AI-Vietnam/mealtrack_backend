"""Put a catalogue dish in plan slots the week writer left empty.

From 2026-11-06 to 2026-11-07 the AI week writer stored weeks with most
lunch and dinner slots empty when a dish could not be written. An empty
slot shows on Home as a meal that cannot be logged (the log answers 422).
The writer now falls back to the catalogue; this does the same for the
weeks it already stored, with the same selection
(``catalog_fallback_slots``): the plan's preferences, the user's
allergies, the catalogue week's choice at that slot.

Only slots that are empty, not logged and not skipped are touched, each
plan in its own transaction. A filled slot moves the plan's revision, so
an open app reloads the week rather than writing over it.

    python scripts/backfill_empty_plan_slots.py --dry-run
    python scripts/backfill_empty_plan_slots.py --plan-id <plan id>
    python scripts/backfill_empty_plan_slots.py --since 2026-11-02
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.app.handlers.command_handlers.meal_planner.write_weekly_plan_dishes_handler import (  # noqa: E501
    catalog_fallback_slots,
)
from src.infra.database.models.weekly_meal_planner.weekly_meal_plan import (
    WeeklyMealPlanORM,
)
from src.infra.database.models.weekly_meal_planner.weekly_meal_plan_slot import (
    WeeklyMealPlanSlotORM,
)
from src.infra.database.uow_async import AsyncUnitOfWork

# A plan with no calorie target of its own gets the planner's usual default.
DEFAULT_DAILY_CALORIES = 2000


def main() -> None:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(
        description="Fill empty weekly plan slots with a catalogue dish."
    )
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        default=date(2026, 11, 2),
        help="Only weeks starting on or after this date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--plan-id", action="append", default=[], help="Only this plan (repeatable)."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show the picks; store none."
    )
    args = parser.parse_args()

    summary = asyncio.run(_run(args))
    for key, value in summary.items():
        print(f"{key}={value}")
    if summary["failed"]:
        raise SystemExit(1)


async def _run(args) -> dict[str, int]:
    plans = await _plans_with_empty_slots(args.since, tuple(args.plan_id))
    filled = left_empty = failed = updated_plans = 0
    for plan_id, user_id in plans:
        try:
            picked, empty = await _fill(plan_id, user_id, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001 - one plan never stops the run
            failed += 1
            print(f"failed plan={plan_id}: {exc!r}", file=sys.stderr)
            continue
        filled += picked
        left_empty += empty - picked
        updated_plans += 1 if picked and not args.dry_run else 0
        print(f"plan={plan_id} empty={empty} filled={picked}")
    return {
        "plans": len(plans),
        "plans_updated": updated_plans,
        "slots_filled": filled,
        "slots_left_empty": left_empty,
        "failed": failed,
    }


async def _plans_with_empty_slots(
    since: date, plan_ids: tuple[str, ...]
) -> list[tuple[str, str]]:
    async with AsyncUnitOfWork() as uow:
        if uow.session is None:
            raise RuntimeError("AsyncUnitOfWork did not initialize a database session")
        stmt = (
            select(WeeklyMealPlanORM.id, WeeklyMealPlanORM.user_id)
            .join(
                WeeklyMealPlanSlotORM,
                WeeklyMealPlanSlotORM.plan_id == WeeklyMealPlanORM.id,
            )
            .where(
                WeeklyMealPlanORM.week_start_date >= since,
                *_empty_slot(),
            )
            .distinct()
            .order_by(WeeklyMealPlanORM.id)
        )
        if plan_ids:
            stmt = stmt.where(WeeklyMealPlanORM.id.in_(plan_ids))
        rows = (await uow.session.execute(stmt)).all()
    return [(str(plan_id), str(user_id)) for plan_id, user_id in rows]


def _empty_slot():
    return (
        WeeklyMealPlanSlotORM.catalog_meal_id.is_(None),
        WeeklyMealPlanSlotORM.saved_suggestion_id.is_(None),
        WeeklyMealPlanSlotORM.is_logged.is_(False),
        WeeklyMealPlanSlotORM.is_skipped.isnot(True),
    )


async def _fill(plan_id: str, user_id: str, *, dry_run: bool) -> tuple[int, int]:
    """Fill one plan's empty slots in one transaction; (filled, empty)."""
    async with AsyncUnitOfWork() as uow:
        plan = await uow.weekly_meal_plans.get_by_id(user_id=user_id, plan_id=plan_id)
        if plan is None:
            return 0, 0
        empty = tuple(
            (slot.day_index, slot.slot_index)
            for slot in plan.slots
            if not slot.has_recipe
            and not slot.is_logged
            and not getattr(slot, "is_skipped", False)
        )
        picks = await catalog_fallback_slots(
            uow,
            user_id=user_id,
            plan_id=plan_id,
            week_start_date=plan.week_start_date,
            daily_calories=plan.daily_calories or DEFAULT_DAILY_CALORIES,
            coordinates=empty,
        )
        for (day, slot), recipe_id in sorted(picks.items()):
            print(f"  plan={plan_id} day={day} slot={slot} -> {recipe_id}")
        if dry_run or not picks:
            return len(picks), len(empty)
        await uow.weekly_meal_plans.update(
            user_id=user_id, plan_id=plan_id, slots=picks
        )
        await uow.commit()
        return len(picks), len(empty)


if __name__ == "__main__":
    main()
