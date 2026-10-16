"""Backfill the entitlement ledger: python -m scripts.backfill_entitlement_ledger.

Candidates are active users ordered by ``users.id``: their Firebase UID plus
any legacy RC ids (lead UUIDs, ``$RCAnonymousID``) recorded in
``users.revenuecat_customer_id`` and ``subscriptions.revenuecat_subscriber_id``.
Only those two id formats are seeded, and never one that is some account's
Firebase UID or another user's billing identity: a legacy TRANSFER rewrote
``revenuecat_subscriber_id`` to the target's id but left ``user_id`` on the
source. Each one runs the production entitlement sync on the ``backfill`` lane.

``--dry-run`` fetches and compares only and writes the JSONL diff report and
nothing else (no ledger rows, no checkpoint). A real run checkpoints the last
finished ``users.id`` to ``--checkpoint`` and resumes from it with ``--resume``.
Run with a modest ``--rps``: the web workers share the same RevenueCat key.

A RevenueCat error is retried after ``--provider-retry-wait`` seconds (long
enough for the budget's circuit breaker to close), up to ``--provider-retries``
times. If the user still fails the run stops there without checkpointing it,
so ``--resume`` starts again with that user instead of skipping everyone the
outage would otherwise have failed in milliseconds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from src.app.services.entitlement_backfill_service import (
    BackfillCandidate,
    EntitlementBackfillService,
    is_seedable_legacy_id,
)
from src.app.services.entitlement_sync_service import EntitlementSyncService
from src.bootstrap.entitlements import get_entitlement_access_config
from src.domain.model.billing.constants import PROVIDER_REVENUECAT
from src.domain.utils.timezone_utils import utc_now
from src.infra.adapters.revenuecat_billing_provider import RevenueCatBillingProvider
from src.infra.config.settings import get_settings
from src.infra.database import config_async
from src.infra.database.models.billing import BillingCustomerIdentityORM
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.services.rc_budget import RcBudget

logger = logging.getLogger(__name__)

DEFAULT_CHECKPOINT = "entitlement_backfill.checkpoint.json"
DEFAULT_REPORT = "entitlement_backfill.report.jsonl"
PROVIDER_ERROR = "provider_error"
# RcBudget keeps its circuit open for 30 s after 5 consecutive failures.
DEFAULT_PROVIDER_RETRY_WAIT = 30.0
DEFAULT_PROVIDER_RETRIES = 3


async def load_candidates(
    session, *, after_user_id: str | None, limit: int, now: datetime
) -> list[BackfillCandidate]:
    """One page of active users with a Firebase UID, ordered by ``users.id``."""
    statement = (
        select(User.id, User.firebase_uid, User.revenuecat_customer_id)
        .where(User.is_active.is_(True), User.firebase_uid.is_not(None))
        .order_by(User.id)
        .limit(limit)
    )
    if after_user_id:
        statement = statement.where(User.id > after_user_id)
    users = (await session.execute(statement)).all()
    if not users:
        return []
    subscriptions = (
        await session.execute(
            select(
                Subscription.user_id,
                Subscription.revenuecat_subscriber_id,
                Subscription.status,
                Subscription.expires_at,
            ).where(Subscription.user_id.in_([row.id for row in users]))
        )
    ).all()
    legacy_ids: dict[str, list[str]] = {}
    legacy_active: dict[str, bool] = {}
    for sub in subscriptions:
        if sub.revenuecat_subscriber_id:
            legacy_ids.setdefault(sub.user_id, []).append(sub.revenuecat_subscriber_id)
        active = sub.status == "active" and (
            sub.expires_at is None or _aware(sub.expires_at) > now
        )
        legacy_active[sub.user_id] = legacy_active.get(sub.user_id, False) or active
    per_user: dict[str, list[str]] = {}
    for row in users:
        ids = [*legacy_ids.get(row.id, [])]
        if row.revenuecat_customer_id:
            ids.insert(0, row.revenuecat_customer_id)
        per_user[row.id] = [
            customer_id
            for customer_id in dict.fromkeys(ids)
            if customer_id != row.firebase_uid and is_seedable_legacy_id(customer_id)
        ]
    owners = await _foreign_owners(
        session, {customer_id for ids in per_user.values() for customer_id in ids}
    )
    return [
        BackfillCandidate(
            user_id=row.id,
            firebase_uid=row.firebase_uid,
            legacy_customer_ids=tuple(
                customer_id
                for customer_id in per_user[row.id]
                if owners.get(customer_id, row.id) == row.id
            ),
            legacy_active=legacy_active.get(row.id, False),
        )
        for row in users
    ]


async def _foreign_owners(session, customer_ids: set[str]) -> dict[str, str | None]:
    """Who already owns each id: a Firebase UID (any account, deleted too)
    belongs to no legacy candidate (None); an identity row to its user."""
    if not customer_ids:
        return {}
    wanted = sorted(customer_ids)
    owners: dict[str, str | None] = {
        row.provider_customer_id: row.user_id
        for row in (
            await session.execute(
                select(
                    BillingCustomerIdentityORM.provider_customer_id,
                    BillingCustomerIdentityORM.user_id,
                ).where(
                    BillingCustomerIdentityORM.provider == PROVIDER_REVENUECAT,
                    BillingCustomerIdentityORM.provider_customer_id.in_(wanted),
                )
            )
        ).all()
    }
    for firebase_uid in (
        await session.execute(
            select(User.firebase_uid).where(User.firebase_uid.in_(wanted))
        )
    ).scalars():
        owners[firebase_uid] = None
    return owners


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass
class Checkpoint:
    path: Path

    def load(self) -> str | None:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        return data.get("last_user_id")

    def save(self, last_user_id: str, processed: int) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "last_user_id": last_user_id,
                    "processed": processed,
                    "updated_at": utc_now().isoformat(),
                }
            )
        )
        os.replace(tmp, self.path)


async def iterate_candidates(
    session_factory: Callable[[], Any],
    *,
    after_user_id: str | None,
    limit: int | None,
    page_size: int,
) -> AsyncIterator[BackfillCandidate]:
    yielded = 0
    cursor = after_user_id
    while limit is None or yielded < limit:
        size = page_size if limit is None else min(page_size, limit - yielded)
        async with session_factory() as session:
            page = await load_candidates(
                session, after_user_id=cursor, limit=size, now=utc_now()
            )
        if not page:
            return
        for candidate in page:
            yield candidate
            yielded += 1
        cursor = page[-1].user_id


def build_service(args: argparse.Namespace) -> EntitlementBackfillService:
    settings = get_settings()
    budget = RcBudget(
        rate_per_second=args.rps or settings.ENTITLEMENT_RC_RPS,
        concurrency=1,
    )
    provider = RevenueCatBillingProvider(api_key=settings.REVENUECAT_SECRET_API_KEY)
    access_config = get_entitlement_access_config()
    return EntitlementBackfillService(
        uow_factory=AsyncUnitOfWork,
        # Same sync service as production, with this run's own --rps budget.
        sync_service=EntitlementSyncService(
            uow_factory=AsyncUnitOfWork,
            provider=provider,
            budget=budget,
            access_config=access_config,
        ),
        provider=provider,
        budget=budget,
        access_config=access_config,
        budget_timeout=args.budget_timeout,
    )


async def backfill_one(
    service: EntitlementBackfillService,
    candidate: BackfillCandidate,
    args: argparse.Namespace,
    *,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> dict[str, Any]:
    """One candidate's report row; provider errors are waited out and retried."""
    attempt = 0
    while True:
        try:
            row = await service.backfill(candidate, dry_run=args.dry_run)
        except Exception as exc:
            logger.exception("backfill failed", extra={"user_id": candidate.user_id})
            return {
                "user_id": candidate.user_id,
                "outcome": "error",
                "error": type(exc).__name__,
            }
        if row.outcome != PROVIDER_ERROR or attempt >= args.provider_retries:
            payload = row.as_dict()
            payload["attempts"] = attempt + 1
            return payload
        attempt += 1
        logger.warning(
            "RevenueCat unavailable during backfill; retrying the same user",
            extra={
                "user_id": candidate.user_id,
                "sync_error": row.sync_error,
                "attempt": attempt,
            },
        )
        await sleep(args.provider_retry_wait)


async def run(
    args: argparse.Namespace,
    *,
    service: EntitlementBackfillService | None = None,
    session_factory: Callable[[], Any] | None = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> dict[str, Any]:
    service = service or build_service(args)
    if session_factory is None:
        if config_async.AsyncSessionLocal is None:
            raise RuntimeError("AsyncSessionLocal is not initialized")
        session_factory = config_async.AsyncSessionLocal
    checkpoint = Checkpoint(Path(args.checkpoint))
    after = args.after_user_id or (checkpoint.load() if args.resume else None)
    counts: Counter[str] = Counter()
    processed = 0
    stopped_at: str | None = None
    report_path = Path(args.report)
    with report_path.open("a" if args.resume else "w") as report:
        async for candidate in iterate_candidates(
            session_factory,
            after_user_id=after,
            limit=args.limit,
            page_size=args.page_size,
        ):
            payload = await backfill_one(service, candidate, args, sleep=sleep)
            counts[payload["outcome"]] += 1
            payload["dry_run"] = args.dry_run
            report.write(json.dumps(payload, sort_keys=True) + "\n")
            report.flush()
            processed += 1
            if payload["outcome"] == PROVIDER_ERROR:
                # RevenueCat is still failing: stop before this user, so the
                # checkpoint never moves past a user that was not fetched.
                stopped_at = candidate.user_id
                logger.error(
                    "Backfill stopped: RevenueCat kept failing; rerun with --resume",
                    extra={"user_id": candidate.user_id},
                )
                break
            if not args.dry_run:
                checkpoint.save(candidate.user_id, processed)
    summary: dict[str, Any] = {
        "dry_run": args.dry_run,
        "processed": processed,
        "outcomes": dict(counts),
        "report": str(report_path),
    }
    if stopped_at is not None:
        summary["stopped_at_user_id"] = stopped_at
    if not args.dry_run:
        summary["checkpoint"] = str(checkpoint.path)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and compare only; write the diff report and nothing else",
    )
    parser.add_argument("--limit", type=int, default=None, help="max users")
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--after-user-id", default=None)
    parser.add_argument(
        "--resume", action="store_true", help="continue from the checkpoint"
    )
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument(
        "--rps", type=float, default=0.5, help="RevenueCat requests/second for this run"
    )
    parser.add_argument("--budget-timeout", type=float, default=120.0)
    parser.add_argument(
        "--provider-retries",
        type=int,
        default=DEFAULT_PROVIDER_RETRIES,
        help="retries of a user whose RevenueCat fetch failed, before stopping",
    )
    parser.add_argument(
        "--provider-retry-wait",
        type=float,
        default=DEFAULT_PROVIDER_RETRY_WAIT,
        help="seconds to wait before each retry (circuit breaker window)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    args = build_parser().parse_args(argv)
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if not 1 <= args.page_size <= 1000:
        raise SystemExit("--page-size must be between 1 and 1000")
    if args.provider_retries < 0 or args.provider_retry_wait < 0:
        raise SystemExit("--provider-retries and --provider-retry-wait must be >= 0")
    print(json.dumps(asyncio.run(run(args)), sort_keys=True))


if __name__ == "__main__":
    main()
