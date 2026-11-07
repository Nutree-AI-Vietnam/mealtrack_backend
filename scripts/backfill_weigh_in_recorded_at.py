"""Move weigh-ins saved with the phone's wall clock back to the moment they name.

Until the app sent an offset, a weigh-in at 13:18 in Vietnam was stored as
20:18 UTC. Such a row sits a whole time-zone offset after its own created_at.
This shifts it back by that offset. A row whose gap is not a clean offset
(queued offline, or already correct) is left alone.

Usage:
  python scripts/backfill_weigh_in_recorded_at.py              # dry run
  python scripts/backfill_weigh_in_recorded_at.py --apply --backup shifts.csv
  python scripts/backfill_weigh_in_recorded_at.py --restore shifts.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select

from src.domain.utils.timezone_utils import ensure_utc
from src.infra.database.models.weight_entry import WeightEntryORM
from src.infra.database.uow_async import AsyncUnitOfWork

# Zone offsets are whole quarter hours and never beyond 14 h.
_OFFSET_STEP = timedelta(minutes=15)
_MAX_OFFSET = timedelta(hours=14)
# The phone stamps the weigh-in a moment before the server saves it.
_TOLERANCE = timedelta(minutes=2)


def wall_clock_offset(recorded_at: datetime, created_at: datetime) -> timedelta | None:
    """The zone offset a weigh-in was stored with, or None if it has none."""
    gap = ensure_utc(recorded_at) - ensure_utc(created_at)
    offset = _OFFSET_STEP * round(gap / _OFFSET_STEP)
    if not offset or abs(offset) > _MAX_OFFSET or abs(gap - offset) > _TOLERANCE:
        return None
    return offset


def planned_shifts(
    rows: list[tuple[str, str, datetime, datetime]],
) -> list[tuple[str, datetime, datetime]]:
    """(id, old, new) for each row to move, skipping moves onto a taken time."""
    taken: dict[str, set[datetime]] = defaultdict(set)
    for _, user_id, recorded_at, _ in rows:
        taken[user_id].add(ensure_utc(recorded_at))
    shifts = []
    for entry_id, user_id, recorded_at, created_at in rows:
        offset = wall_clock_offset(recorded_at, created_at)
        if offset is None:
            continue
        old = ensure_utc(recorded_at)
        new = old - offset
        if new in taken[user_id]:
            continue
        taken[user_id].discard(old)
        taken[user_id].add(new)
        shifts.append((entry_id, old, new))
    return shifts


async def backfill(*, apply: bool, backup: str | None) -> int:
    async with AsyncUnitOfWork() as uow:
        result = await uow.session.execute(
            select(
                WeightEntryORM.id,
                WeightEntryORM.user_id,
                WeightEntryORM.recorded_at,
                WeightEntryORM.created_at,
            )
        )
        shifts = planned_shifts([tuple(row) for row in result.all()])
        for entry_id, old, new in shifts[:20]:
            print(f"{entry_id}: {old.isoformat()} -> {new.isoformat()}")
        if not apply:
            return len(shifts)
        if not backup:
            raise SystemExit("--apply needs --backup <csv>")
        with open(backup, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "recorded_at"])
            writer.writerows((entry_id, old.isoformat()) for entry_id, old, _ in shifts)
        rows = {
            entry.id: entry
            for entry in (
                await uow.session.execute(
                    select(WeightEntryORM).where(
                        WeightEntryORM.id.in_([s[0] for s in shifts])
                    )
                )
            ).scalars()
        }
        for entry_id, _, new in shifts:
            rows[entry_id].recorded_at = new
        await uow.commit()
        return len(shifts)


async def restore(path: str) -> int:
    with open(path, newline="") as handle:
        saved = {
            row["id"]: datetime.fromisoformat(row["recorded_at"])
            for row in csv.DictReader(handle)
        }
    async with AsyncUnitOfWork() as uow:
        entries = (
            await uow.session.execute(
                select(WeightEntryORM).where(WeightEntryORM.id.in_(list(saved)))
            )
        ).scalars()
        count = 0
        for entry in entries:
            entry.recorded_at = saved[entry.id]
            count += 1
        await uow.commit()
        return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", help="CSV of the old times, written before --apply")
    parser.add_argument("--restore", help="CSV from an earlier --apply to put back")
    args = parser.parse_args()

    if args.restore:
        print(f"restored {asyncio.run(restore(args.restore))} weigh-in(s)")
        return
    count = asyncio.run(backfill(apply=args.apply, backup=args.backup))
    print(f"{'moved' if args.apply else 'would move'} {count} weigh-in(s)")


if __name__ == "__main__":
    main()
