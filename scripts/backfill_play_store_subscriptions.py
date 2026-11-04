"""Backfill Play Store subscriptions that never reached the subscriptions table.

The production RevenueCat webhook that keeps ``subscriptions`` was filtered to
the App Store app, so no Google Play purchase ever created a row. For each
listed customer this script reads RevenueCat and inserts the row the webhook
would have kept: one per RevenueCat subscriber, for its latest production Play
subscription, with that subscription's current status.

It only inserts, and sends no events, pushes or referral credits. A customer
who already has a row is reported and left alone, because a webhook owns that
row. Without ``--apply`` it only reports, one JSON line per customer.

Pass RevenueCat app user IDs (Firebase UIDs) with ``--uids-file``, one per
line, ``#`` comments allowed. RevenueCat's customer read creates unknown
customers, so list only people who paid. Turn on the Play Store webhook first,
so a purchase made after this run is not missed.

Usage:
  python -m scripts.backfill_play_store_subscriptions --uids-file uids.txt \
      --expect-database HOST/DBNAME [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Select, or_, select

from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork

MAX_CONSECUTIVE_ERRORS = 3
REVENUECAT_TIMEOUT_SECONDS = 10.0
# Firebase UIDs; also keeps the ID safe inside the RevenueCat URL path.
APP_USER_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class Outcome(StrEnum):
    NO_USER = "skipped_no_user"
    ROW_EXISTS = "skipped_row_exists"
    NO_PLAY = "skipped_no_play_subscription"
    PENDING = "pending_insert"
    INSERTED = "inserted"
    ERROR = "error"


@dataclass(frozen=True)
class PlaySubscription:
    product_id: str
    status: str
    purchased_at: datetime
    expires_at: datetime | None
    cancelled_at: datetime | None
    store_transaction_id: str | None


def parse_uids_file(lines: Iterable[str]) -> list[str]:
    """One app user ID per line, first occurrence kept."""
    app_user_ids: dict[str, None] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not APP_USER_ID.fullmatch(line):
            raise ValueError(f"Expected a Firebase UID, got {line!r}")
        app_user_ids.setdefault(line, None)
    return list(app_user_ids)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def subscription_status(entry: dict, now: datetime) -> str:
    """The status the lifecycle webhooks would have left on the row."""
    expires = parse_time(entry.get("expires_date"))
    grace = parse_time(entry.get("grace_period_expires_date"))
    if entry.get("refunded_at"):
        return "refunded"
    lapsed = expires is not None and max(expires, grace or expires) <= now
    if entry.get("billing_issues_detected_at") and not lapsed:
        return "billing_issue"
    if expires is not None and expires <= now:
        return "expired"
    if entry.get("unsubscribe_detected_at"):
        return "cancelled"
    return "active"


def latest_play_subscription(
    subscriber_info: dict | None, now: datetime
) -> PlaySubscription | None:
    """The production Play entry that expires last, shaped like a webhook row."""
    subscriber = (subscriber_info or {}).get("subscriber") or {}
    entries = [
        (key, entry)
        for key, entry in (subscriber.get("subscriptions") or {}).items()
        if entry.get("store") == "play_store" and not entry.get("is_sandbox")
    ]
    if not entries:
        return None
    key, entry = max(
        entries,
        key=lambda item: (
            parse_time(item[1].get("expires_date")) or datetime.max.replace(tzinfo=UTC)
        ),
    )
    # Webhooks send "subscription:base_plan"; this read splits the plan out.
    plan = entry.get("product_plan_identifier")
    transaction = entry.get("store_transaction_id")
    return PlaySubscription(
        product_id=f"{key}:{plan}" if plan else key,
        status=subscription_status(entry, now),
        purchased_at=parse_time(entry.get("original_purchase_date"))
        or parse_time(entry.get("purchase_date"))
        or now,
        expires_at=parse_time(entry.get("expires_date")),
        cancelled_at=parse_time(entry.get("unsubscribe_detected_at")),
        store_transaction_id=str(transaction) if transaction else None,
    )


def subscription_row(
    user_id: str, app_user_id: str, play: PlaySubscription
) -> Subscription:
    # created_at is the first purchase, as on rows the webhook created then.
    return Subscription(
        id=str(uuid.uuid4()),
        user_id=user_id,
        revenuecat_subscriber_id=app_user_id,
        product_id=play.product_id,
        platform="android",
        status=play.status,
        purchased_at=play.purchased_at,
        expires_at=play.expires_at,
        cancelled_at=play.cancelled_at,
        store_transaction_id=play.store_transaction_id,
        is_sandbox=False,
        created_at=play.purchased_at,
    )


def user_query(app_user_id: str) -> Select:
    return select(User.id).where(
        User.firebase_uid == app_user_id, User.is_active.is_(True)
    )


def existing_row_query(app_user_id: str, user_id: str) -> Select:
    """Any row a webhook would update, by RevenueCat ID or by user."""
    return select(Subscription.id).where(
        or_(
            Subscription.revenuecat_subscriber_id == app_user_id,
            Subscription.user_id == user_id,
        )
    )


async def find_user_id(app_user_id: str) -> str | None:
    async with AsyncUnitOfWork.read_only() as uow:
        result = await uow.session.execute(user_query(app_user_id))
        return result.scalars().first()


async def has_row(app_user_id: str, user_id: str) -> bool:
    async with AsyncUnitOfWork.read_only() as uow:
        query = existing_row_query(app_user_id, user_id).limit(1)
        return (await uow.session.execute(query)).first() is not None


async def insert_row(user_id: str, app_user_id: str, play: PlaySubscription) -> bool:
    """Insert unless a webhook wrote a row since the check; True when inserted."""
    async with AsyncUnitOfWork() as uow:
        query = existing_row_query(app_user_id, user_id)
        if (await uow.session.execute(query.limit(1))).first() is not None:
            return False
        uow.session.add(subscription_row(user_id, app_user_id, play))
        await uow.commit()
    return True


async def fetch_subscriber(app_user_id: str) -> dict | None:
    """Raises on HTTP errors, so an outage is never read as "no purchase"."""
    return await RevenueCatAdapter()._fetch_subscriber(
        app_user_id, timeout=REVENUECAT_TIMEOUT_SECONDS
    )


async def run(
    app_user_ids: list[str],
    *,
    apply: bool,
    emit: Callable[[dict[str, object]], None],
    now: datetime | None = None,
    find_user: Callable[[str], Awaitable[str | None]] = find_user_id,
    row_exists: Callable[[str, str], Awaitable[bool]] = has_row,
    fetch: Callable[[str], Awaitable[dict | None]] = fetch_subscriber,
    insert: Callable[[str, str, PlaySubscription], Awaitable[bool]] = insert_row,
) -> Counter[str]:
    """Decides each customer in turn and emits its row before moving on."""
    counts: Counter[str] = Counter()
    consecutive_errors = 0
    for app_user_id in app_user_ids:
        row: dict[str, object] = {"app_user_id": app_user_id}
        try:
            user_id = await find_user(app_user_id)
            row["user_id"] = user_id
            if user_id is None:
                outcome = Outcome.NO_USER
            elif await row_exists(app_user_id, user_id):
                outcome = Outcome.ROW_EXISTS
            else:
                play = latest_play_subscription(
                    await fetch(app_user_id), now or datetime.now(UTC)
                )
                if play is None:
                    outcome = Outcome.NO_PLAY
                else:
                    row["product_id"] = play.product_id
                    row["status"] = play.status
                    row["expires_at"] = play.expires_at and play.expires_at.isoformat()
                    if apply and not await insert(user_id, app_user_id, play):
                        outcome = Outcome.ROW_EXISTS
                    else:
                        outcome = Outcome.INSERTED if apply else Outcome.PENDING
                        counts[f"status:{play.status}"] += 1
            consecutive_errors = 0
        except Exception as error:  # noqa: BLE001 - recorded per customer, then bounded
            outcome = Outcome.ERROR
            row["error"] = f"{type(error).__name__}: {error}"[:300]
            consecutive_errors += 1
        row["outcome"] = outcome.value
        counts[outcome.value] += 1
        emit(row)
        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            emit({"stopped": f"{MAX_CONSECUTIVE_ERRORS} consecutive errors"})
            break
    return counts


async def main() -> None:
    # Imported here: tests load this file by path, where `scripts` is shadowed.
    from scripts.delete_missed_firebase_accounts import check_database_target

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uids-file", required=True)
    parser.add_argument("--expect-database", required=True, help="HOST/DBNAME")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    database = check_database_target(args.expect_database)
    if not os.getenv("REVENUECAT_SECRET_API_KEY"):
        raise SystemExit("Set REVENUECAT_SECRET_API_KEY")
    with open(args.uids_file, encoding="utf-8") as handle:
        app_user_ids = parse_uids_file(handle)
    print(
        json.dumps(
            {"database": database, "apply": args.apply, "customers": len(app_user_ids)}
        ),
        flush=True,
    )

    counts = await run(
        app_user_ids,
        apply=args.apply,
        emit=lambda row: print(json.dumps(row), flush=True),
    )
    print(json.dumps({"apply": args.apply, "counts": dict(counts)}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
