#!/usr/bin/env python3
"""Reconcile local access facts from RevenueCat; dry-run unless --apply is set."""

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import or_, select

from src.domain.ports.subscription_service_port import RevenueCatSnapshotUnavailable
from src.domain.services.subscription_access_policy import (
    SubscriptionAccessUnavailable,
    evaluate_subscription_access,
)
from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter
from src.infra.database.config_async import AsyncSessionLocal
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.services.subscription_access_sync_service import (
    normalize_standard_access_facts,
    sync_revenuecat_access,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all-active", action="store_true")
    scope.add_argument("--user-id")
    parser.add_argument("--apply", action="store_true", help="Persist verified changes")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


async def _candidate_ids(user_id: str | None) -> list[str]:
    async with AsyncSessionLocal() as session:
        if user_id:
            rows = await session.scalars(select(User.id).where(User.id == user_id))
        else:
            rows = await session.scalars(
                select(User.id)
                .outerjoin(Subscription, Subscription.user_id == User.id)
                .where(
                    or_(User.is_active.is_(True), Subscription.id.is_not(None)),
                    User.deleted_at.is_(None),
                )
                .distinct()
                .order_by(User.id)
            )
        return list(rows.all())


async def _load_candidate(user_id: str):
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None or not user.firebase_uid:
            return None
        rows = list(
            (
                await session.scalars(
                    select(Subscription).where(Subscription.user_id == user.id)
                )
            ).all()
        )
        return user.id, user.firebase_uid, user.updated_at, rows


async def _fetch_with_retry(
    adapter: RevenueCatAdapter, uid: str, last_call: list[float]
):
    for attempt in range(3):
        wait = 1.0 - (time.monotonic() - last_call[0])
        if wait > 0:
            await asyncio.sleep(wait)
        last_call[0] = time.monotonic()
        try:
            return await adapter.get_verified_snapshot(uid)
        except RevenueCatSnapshotUnavailable as error:
            if attempt == 2:
                raise
            delay = error.retry_after_seconds or float(2**attempt)
            await asyncio.sleep(min(max(delay, 0), 120))
    raise RuntimeError("unreachable")


def _dry_run_result(snapshot, rows) -> str:
    if snapshot.absent:
        return (
            "unverified"
            if any(row.provider == "revenuecat" for row in rows)
            else "deny"
        )
    facts = normalize_standard_access_facts(snapshot.subscriber, datetime.now(UTC))
    normalized = []
    if facts and facts["status"] != "expired":
        normalized = [
            SimpleNamespace(
                status=facts["status"],
                expires_at=facts["expires_at"],
                grace_period_expires_at=facts["grace_period_expires_at"],
                period_type=facts["period_type"],
                provider_snapshot_at=snapshot.request_date,
            )
        ]
    try:
        return (
            "grant" if evaluate_subscription_access(normalized).has_access else "deny"
        )
    except SubscriptionAccessUnavailable:
        return "unverified"


async def _run() -> int:
    args = _args()
    if AsyncSessionLocal is None:
        raise RuntimeError("Database is not configured")
    completed: set[str] = set()
    if args.resume:
        if not args.checkpoint or not args.checkpoint.exists():
            raise ValueError("--resume requires an existing --checkpoint")
        completed = set(json.loads(args.checkpoint.read_text()).get("completed", []))

    adapter = RevenueCatAdapter()
    last_call = [0.0]
    counts = {"grant": 0, "deny": 0, "unverified": 0, "skipped": 0}
    for user_id in await _candidate_ids(args.user_id):
        if user_id in completed:
            counts["skipped"] += 1
            continue
        candidate = await _load_candidate(user_id)
        if candidate is None:
            counts["unverified"] += 1
            continue
        database_id, firebase_uid, updated_at, rows = candidate
        try:
            snapshot = await _fetch_with_retry(adapter, firebase_uid, last_call)
            outcome = _dry_run_result(snapshot, rows)
            counts[outcome] += 1
            if args.apply:
                async with AsyncSessionLocal() as session:
                    user = await session.get(User, database_id)
                    if user is None:
                        raise RevenueCatSnapshotUnavailable(
                            "Subscription owner disappeared"
                        )
                    await sync_revenuecat_access(
                        session,
                        user,
                        adapter=adapter,
                        snapshot=snapshot,
                        expected_updated_at=updated_at,
                    )
                    await session.commit()
            completed.add(user_id)
        except RevenueCatSnapshotUnavailable:
            counts["unverified"] += 1
        if args.checkpoint:
            args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
            args.checkpoint.write_text(
                json.dumps({"completed": sorted(completed)}, indent=2) + "\n"
            )

    mode = "apply" if args.apply else "dry-run"
    print(
        f"mode={mode} grant={counts['grant']} deny={counts['deny']} "
        f"unverified={counts['unverified']} skipped={counts['skipped']}"
    )
    return 1 if counts["unverified"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
