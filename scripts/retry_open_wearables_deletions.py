"""Retry Open Wearables deletions for deleted accounts that are still linked.

Account deletion commits first and then deletes the person's Open Wearables
user. When that call fails, `users.open_wearables_user_id` stays on the deleted
row. This script lists those rows and, with --execute, retries them.

Usage:
    python scripts/retry_open_wearables_deletions.py            # List only (default)
    python scripts/retry_open_wearables_deletions.py --execute  # Retry the deletions
    python scripts/retry_open_wearables_deletions.py --execute --limit 50

Needs the backend's database settings. --execute also needs
OPEN_WEARABLES_API_URL and OPEN_WEARABLES_API_KEY. Exits with status 1 when a
retry fails, so the run can be repeated until it exits 0.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select

from src.app.services.open_wearables_account_cleanup import (
    delete_open_wearables_user,
)
from src.bootstrap.integration_services import get_wearable_account_service
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.wearable_account_port import WearableAccountPort
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork


async def find_outstanding_links(limit: int | None) -> list[tuple[str, str]]:
    """(user id, Open Wearables user id) for deleted accounts still linked."""
    async with AsyncUnitOfWork.read_only() as uow:
        stmt = (
            select(User.id, User.open_wearables_user_id)
            .where(
                User.is_active.is_(False),
                User.open_wearables_user_id.is_not(None),
            )
            .order_by(User.deleted_at.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await uow.session.execute(stmt)).all()
    return [(str(user_id), ow_user_id) for user_id, ow_user_id in rows]


async def retry_deletions(
    rows: list[tuple[str, str]],
    wearable_accounts: WearableAccountPort,
    uow_factory: Callable[[], AsyncUnitOfWorkPort] = AsyncUnitOfWork,
) -> tuple[int, int]:
    """Retry each row. Returns (deleted, still linked)."""
    deleted = 0
    still_linked = 0
    for user_id, ow_user_id in rows:
        if await delete_open_wearables_user(
            wearable_accounts, uow_factory(), UUID(user_id), ow_user_id
        ):
            deleted += 1
        else:
            still_linked += 1
            print(f"{user_id}: still linked")
    return deleted, still_linked


async def run(*, execute: bool, limit: int | None) -> int:
    rows = await find_outstanding_links(limit)
    print(f"{len(rows)} deleted account(s) still linked to Open Wearables")
    if not execute or not rows:
        return 0

    wearable_accounts = get_wearable_account_service()
    if wearable_accounts is None:
        print("Set OPEN_WEARABLES_API_URL and OPEN_WEARABLES_API_KEY to retry")
        return 1

    deleted, still_linked = await retry_deletions(rows, wearable_accounts)
    print(f"deleted {deleted}, still linked {still_linked}")
    return 1 if still_linked else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Retry the deletions. Without it the script only lists them.",
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be greater than 0")

    raise SystemExit(asyncio.run(run(execute=args.execute, limit=args.limit)))


if __name__ == "__main__":
    main()
