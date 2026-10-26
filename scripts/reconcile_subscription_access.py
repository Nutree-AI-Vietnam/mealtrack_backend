#!/usr/bin/env python3
"""Reconcile RevenueCat access rows from verified RevenueCat customers.

Dry run unless --apply. Runbook: docs/runbooks/subscription-access-reconciliation.md.

Populations (pick one; the default is --subscribers):
  --subscribers   users holding at least one provider='revenuecat' subscriptions
                  row. Only these are read, because RevenueCat's GET /subscribers
                  is "get or create": reading anyone else creates an empty
                  RevenueCat customer for them.
  --user-id ID    one database user.
  --repair        users whose RevenueCat rows need a fresh read now: unverified
                  grants (provider_snapshot_at NULL), rows a failed webhook read
                  left unresolved (provider_sync_required_at set), rows whose
                  expiry or grace deadline is within --expiring-within-hours of
                  now (either side), and, with --modified-since, rows changed
                  since then (e.g. by an image rolled back past this code).
                  Meant to be scheduled (hourly) with --apply.
  --all-users     every active user. Opt-in only: it creates an empty
                  RevenueCat customer for every user who never bought.

Deleted and deactivated accounts are never read (their Firebase uid is a
tombstone).

Each user is read from RevenueCat outside any transaction, then the snapshot
is applied with sync_revenuecat_access inside one transaction: committed with
--apply, rolled back otherwise. The dry run therefore predicts the apply
exactly and writes nothing.

Output: one JSONL record per user in --output (internal user id, decision,
reason, snapshot status, previous decision; no emails, Firebase uids or
provider payloads), unresolved users and downgrades on stdout, and a final
summary line:

  RESULT: GO|NO-GO|INCOMPLETE mode=... users=... granted=... denied=...
          unresolved=... downgraded=... failed=... skipped=...

Exit codes: 0 GO (no user left unresolved), 1 NO-GO (unresolved users remain),
2 usage or configuration error, 3 stopped early (RevenueCat asked to wait
longer than --max-wait-seconds, kept failing, or the run was interrupted);
continue with --resume.

--gate runs only the SQL go/no-go check (GO_NO_GO_SQL; no RevenueCat reads):
exit 0 when no active user holds an unverified RevenueCat grant or a row left
unresolved by a failed webhook read, else 1.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from collections.abc import Awaitable, Callable, Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

from sqlalchemy import Select, and_, or_, select, text

# Run from the repository root either as `python -m scripts.<name>` or by path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.domain.model.subscription_access import (
    SubscriptionAccessUnavailable,
    evaluate_subscription_access,
)
from src.domain.ports.subscription_service_port import (
    SNAPSHOT_REASON_HTTP_5XX,
    SNAPSHOT_REASON_HTTP_429,
    SNAPSHOT_REASON_OWNER_CHANGED,
    SNAPSHOT_REASON_TRANSPORT,
    RevenueCatSnapshot,
    RevenueCatSnapshotUnavailable,
)
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.services.subscription_access_sync_service import (
    ACCESS_DENIED,
    ACCESS_GRANTED,
    ACCESS_UNRESOLVED,
    GRANTING_STATUSES,
    REVENUECAT_PROVIDER,
    SubscriptionSyncOutcome,
    sync_revenuecat_access,
)

EXIT_GO = 0
EXIT_NO_GO = 1
EXIT_USAGE = 2
EXIT_INCOMPLETE = 3

# The cutover gate (docs/runbooks/subscription-access-reconciliation.md quotes
# it verbatim). Both counts must be 0 before SUBSCRIPTION_ACCESS_ENABLED goes
# on: either kind of row makes the access policy answer "unresolved" (503
# ENTITLEMENTS_UNAVAILABLE) for a user nothing else grants.
GO_NO_GO_SQL = """\
SELECT
  count(*) FILTER (
    WHERE s.status IN ('active', 'cancelled', 'billing_issue')
      AND s.provider_snapshot_at IS NULL
  ) AS unverified_grants,
  count(*) FILTER (
    WHERE s.provider_sync_required_at IS NOT NULL
  ) AS sync_required
FROM subscriptions AS s
JOIN users AS u ON u.id = s.user_id
WHERE s.provider = 'revenuecat'
  AND u.is_active
  AND u.deleted_at IS NULL;"""

CHECKPOINT_VERSION = 1
# A checkpoint older than this is refused: rows may have changed since
# (webhooks, an image rollback), so users it marks done need another read.
CHECKPOINT_MAX_AGE = timedelta(hours=24)
# Provider failures worth retrying; anything else is a contract or
# configuration problem that a retry cannot fix.
RETRYABLE_REASONS = frozenset(
    {SNAPSHOT_REASON_TRANSPORT, SNAPSHOT_REASON_HTTP_429, SNAPSHOT_REASON_HTTP_5XX}
)
# Stop instead of marking every remaining user failed during an outage.
MAX_CONSECUTIVE_PROVIDER_FAILURES = 5

POPULATION_SUBSCRIBERS = "subscribers"
POPULATION_USER = "user"
POPULATION_REPAIR = "repair"
POPULATION_ALL_USERS = "all-users"

ALL_USERS_WARNING = (
    "WARNING: --all-users reads every active user from RevenueCat. "
    "GET /v1/subscribers is get-or-create, so this creates an empty "
    "RevenueCat customer for every user who never bought."
)

SessionFactory = Callable[[], AbstractAsyncContextManager[Any]]
SyncFunction = Callable[..., Awaitable[SubscriptionSyncOutcome]]


class UsageError(Exception):
    """Arguments or a checkpoint that this run must not proceed with."""


class StopRun(Exception):
    """Stop the pass early; the checkpoint lets --resume continue it."""


# --- Population -------------------------------------------------------------


@dataclass(frozen=True)
class Population:
    kind: str
    user_id: str | None = None
    expiring_within_hours: float = 0
    modified_since: datetime | None = None

    @property
    def key(self) -> str:
        """Identifies the population in a checkpoint header."""
        if self.kind == POPULATION_USER:
            return f"user:{self.user_id}"
        if self.kind == POPULATION_REPAIR:
            since = self.modified_since.isoformat() if self.modified_since else "-"
            return f"repair:hours={self.expiring_within_hours:g}:since={since}"
        return self.kind


def population_statement(population: Population, now: datetime) -> Select:
    """Database user ids to read from RevenueCat, in a stable order."""
    if population.kind == POPULATION_USER:
        return select(User.id).where(User.id == population.user_id)
    # Deleted or deactivated accounts carry a tombstoned Firebase uid.
    statement = select(User.id).where(
        User.is_active.is_(True), User.deleted_at.is_(None)
    )
    if population.kind == POPULATION_ALL_USERS:
        return statement.order_by(User.id)

    revenuecat_row = [
        Subscription.user_id == User.id,
        Subscription.provider == REVENUECAT_PROVIDER,
    ]
    if population.kind == POPULATION_REPAIR:
        revenuecat_row.append(or_(*repair_conditions(population, now)))
    elif population.kind != POPULATION_SUBSCRIBERS:
        raise ValueError(f"unknown population {population.kind}")
    return statement.where(
        select(Subscription.id).where(*revenuecat_row).exists()
    ).order_by(User.id)


def repair_conditions(population: Population, now: datetime) -> list[Any]:
    """Row conditions that make a user worth a fresh RevenueCat read."""
    conditions: list[Any] = [
        # A webhook changed access but its RevenueCat read failed.
        Subscription.provider_sync_required_at.is_not(None),
        # A possible grant no snapshot ever verified (legacy or old-image row).
        and_(
            Subscription.status.in_(sorted(GRANTING_STATUSES)),
            Subscription.provider_snapshot_at.is_(None),
        ),
    ]
    if population.expiring_within_hours > 0:
        window = timedelta(hours=population.expiring_within_hours)
        start, end = now - window, now + window
        # Renewals, expirations and billing retries land around these
        # deadlines; a lost webhook shows up here first.
        conditions.append(Subscription.expires_at.between(start, end))
        conditions.append(Subscription.grace_period_expires_at.between(start, end))
    if population.modified_since is not None:
        conditions.append(Subscription.updated_at >= population.modified_since)
    return conditions


async def candidate_ids(
    session_factory: SessionFactory, population: Population, now: datetime
) -> list[str]:
    async with session_factory() as session:
        rows = await session.scalars(population_statement(population, now))
        return [str(user_id) for user_id in rows.all()]


# --- RevenueCat pacing ------------------------------------------------------


class Pacer:
    """At most one RevenueCat request per interval; honours Retry-After."""

    def __init__(
        self,
        min_interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ):
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._next_at = float("-inf")

    async def wait(self) -> None:
        delay = self._next_at - self._clock()
        if delay > 0:
            await self._sleep(delay)
        self._next_at = self._clock() + self.min_interval

    def back_off(self, seconds: float) -> None:
        """Hold every following request for at least ``seconds``."""
        self._next_at = max(self._next_at, self._clock() + seconds)


async def read_snapshot(
    adapter: Any,
    firebase_uid: str,
    pacer: Pacer,
    *,
    max_attempts: int,
    max_wait_seconds: float,
) -> RevenueCatSnapshot:
    """Read one customer, retrying transient failures within RevenueCat's limits.

    A Retry-After (or 429) longer than ``max_wait_seconds`` stops the pass.
    """
    for attempt in range(1, max_attempts + 1):
        await pacer.wait()
        try:
            return await adapter.get_verified_snapshot(firebase_uid)
        except RevenueCatSnapshotUnavailable as error:
            reason = getattr(error, "reason", None)
            retry_after = getattr(error, "retry_after_seconds", None)
            if retry_after is not None and retry_after > max_wait_seconds:
                raise StopRun(
                    f"RevenueCat asked to wait {retry_after:g}s "
                    f"(more than --max-wait-seconds={max_wait_seconds:g})"
                ) from error
            if reason not in RETRYABLE_REASONS:
                raise
            # Held for the next request, also the next user's after the
            # last attempt: RevenueCat's Retry-After applies to the API key.
            delay = retry_after if retry_after is not None else 2.0**attempt
            pacer.back_off(min(delay, max_wait_seconds))
            if attempt == max_attempts:
                raise
    raise AssertionError("unreachable")


# --- One user ---------------------------------------------------------------


@dataclass
class UserResult:
    """One user's outcome; written to --output as one JSONL record."""

    user_id: str
    mode: str
    decision: str  # granted | denied | unresolved, after this run
    before: str  # the stored decision before this run
    reason: str  # sync result, or the failure reason code
    snapshot: str  # ok | absent | unavailable | not_read
    downgrade: bool = False
    rows_revoked: int = 0
    row_created: bool = False
    markers_cleared: int = 0
    standard_missing: bool = False

    @property
    def failed(self) -> bool:
        """The customer could not be read or its snapshot was not applied."""
        return self.snapshot in {"unavailable", "not_read"} or self.reason not in {
            "applied",
            "unchanged",
            "stale_skipped",
            "absent",
        }


def _is_live(user: Any) -> bool:
    return bool(user.is_active) and user.deleted_at is None


def stored_decision(rows: Iterable[Any]) -> str:
    try:
        granted = evaluate_subscription_access(rows).has_access
    except SubscriptionAccessUnavailable:
        return ACCESS_UNRESOLVED
    return ACCESS_GRANTED if granted else ACCESS_DENIED


async def _load_user(session: Any, user_id: str) -> tuple[Any, list[Any]]:
    user = await session.get(User, user_id)
    if user is None:
        return None, []
    rows = await session.scalars(
        select(Subscription).where(
            Subscription.user_id == user.id,
            Subscription.provider == REVENUECAT_PROVIDER,
        )
    )
    return user, list(rows.all())


async def reconcile_user(
    user_id: str,
    *,
    apply: bool,
    session_factory: SessionFactory,
    adapter: Any,
    pacer: Pacer,
    sync: SyncFunction = sync_revenuecat_access,
    max_attempts: int = 3,
    max_wait_seconds: float = 600,
) -> UserResult:
    mode = "apply" if apply else "dry-run"
    async with session_factory() as session:
        user, rows = await _load_user(session, user_id)
        before = stored_decision(rows)
        firebase_uid = getattr(user, "firebase_uid", None)
        inactive = user is not None and not _is_live(user)
    if user is None or inactive or not firebase_uid:
        # A deleted or deactivated account carries a tombstoned Firebase uid:
        # reading it would only create an empty RevenueCat customer.
        reason = "user_missing" if user is None else "user_inactive"
        if user is not None and not inactive:
            reason = "no_firebase_uid"
        return UserResult(user_id, mode, before, before, reason, "not_read")

    # No transaction (and no row lock) is open across the HTTP call.
    try:
        snapshot = await read_snapshot(
            adapter,
            firebase_uid,
            pacer,
            max_attempts=max_attempts,
            max_wait_seconds=max_wait_seconds,
        )
    except RevenueCatSnapshotUnavailable as error:
        reason = getattr(error, "reason", None) or "unknown"
        return UserResult(user_id, mode, before, before, reason, "unavailable")

    snapshot_status = "absent" if snapshot.absent else "ok"
    async with session_factory() as session:
        user = await session.get(User, user_id)
        try:
            if user is None:
                raise RevenueCatSnapshotUnavailable(
                    "Reconciled user disappeared", reason=SNAPSHOT_REASON_OWNER_CHANGED
                )
            outcome = await sync(
                session,
                user,
                snapshot=snapshot,
                source="reconcile" if apply else "reconcile_dry_run",
            )
        except RevenueCatSnapshotUnavailable as error:
            # Readable but unsafe to apply (owner changed, conflicting facts,
            # absence over a possible grant): the rows stay as they were.
            await session.rollback()
            reason = getattr(error, "reason", None) or "unknown"
            return UserResult(user_id, mode, before, before, reason, snapshot_status)
        if apply:
            await session.commit()
        else:
            await session.rollback()
    return UserResult(
        user_id,
        mode,
        decision=outcome.access_after,
        before=outcome.access_before,
        reason=outcome.result,
        snapshot=snapshot_status,
        downgrade=outcome.downgraded,
        rows_revoked=outcome.rows_revoked,
        row_created=outcome.row_created,
        markers_cleared=outcome.markers_cleared,
        standard_missing=outcome.standard_missing,
    )


# --- Checkpoint and output --------------------------------------------------


def _private_append(path: Path, *, truncate: bool) -> TextIO:
    """Open a file only the operator can read: it holds internal user ids."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if truncate else os.O_APPEND)
    return os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8")


def _json_line(stream: TextIO, record: dict[str, Any]) -> None:
    stream.write(json.dumps(record, sort_keys=True) + "\n")
    stream.flush()


class Checkpoint:
    """Append-only JSONL: a header naming the pass, then one line per user done.

    A torn last line (the process killed mid-write) is ignored on resume.
    """

    def __init__(self, path: Path, header: dict[str, Any], completed: set[str]):
        self.path = path
        self.header = header
        self.completed = completed
        self._stream: TextIO | None = None

    @classmethod
    def start(
        cls, path: Path, *, mode: str, population: Population, now: datetime
    ) -> Checkpoint:
        header = {
            "checkpoint": CHECKPOINT_VERSION,
            "mode": mode,
            "population": population.key,
            "started_at": now.isoformat(),
        }
        checkpoint = cls(path, header, set())
        checkpoint._stream = _private_append(path, truncate=True)
        _json_line(checkpoint._stream, header)
        return checkpoint

    @classmethod
    def resume(
        cls, path: Path, *, mode: str, population: Population, now: datetime
    ) -> Checkpoint:
        if not path.exists():
            raise UsageError(f"--resume: checkpoint {path} does not exist")
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        try:
            header = json.loads(lines[0]) if lines else {}
        except json.JSONDecodeError:
            header = {}
        if header.get("checkpoint") != CHECKPOINT_VERSION:
            raise UsageError(
                f"--resume: {path} is not a reconcile checkpoint "
                f"(version {CHECKPOINT_VERSION}); start a fresh pass"
            )
        if header.get("mode") != mode:
            raise UsageError(
                f"--resume: {path} was written by a {header.get('mode')} pass; "
                f"a {mode} pass cannot resume it. Start a fresh {mode} pass "
                "(omit --resume) or use its own checkpoint path."
            )
        if header.get("population") != population.key:
            raise UsageError(
                f"--resume: {path} covers population {header.get('population')!r}, "
                f"not {population.key!r}; start a fresh pass"
            )
        started_at = datetime.fromisoformat(header["started_at"])
        if now - started_at > CHECKPOINT_MAX_AGE:
            raise UsageError(
                f"--resume: {path} started at {header['started_at']}, more than "
                f"{CHECKPOINT_MAX_AGE} ago; rows may have changed since, so "
                "start a fresh pass"
            )
        completed: set[str] = set()
        for line in lines[1:]:
            try:
                completed.add(str(json.loads(line)["user_id"]))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue  # torn final line
        checkpoint = cls(path, header, completed)
        checkpoint._stream = _private_append(path, truncate=False)
        if not text.endswith("\n"):
            # End the torn line so the next record starts on its own line.
            checkpoint._stream.write("\n")
        return checkpoint

    def record(self, user_id: str) -> None:
        self.completed.add(user_id)
        if self._stream is not None:
            _json_line(self._stream, {"user_id": user_id})

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None


# --- Pass -------------------------------------------------------------------


@dataclass
class Summary:
    mode: str
    population: str
    counts: dict[str, int] = field(
        default_factory=lambda: {
            "users": 0,
            ACCESS_GRANTED: 0,
            ACCESS_DENIED: 0,
            ACCESS_UNRESOLVED: 0,
            "downgraded": 0,
            "failed": 0,
            "skipped": 0,
        }
    )
    stopped: str | None = None

    def add(self, result: UserResult) -> None:
        self.counts["users"] += 1
        self.counts[result.decision] += 1
        self.counts["downgraded"] += result.downgrade
        self.counts["failed"] += result.failed

    @property
    def exit_code(self) -> int:
        if self.stopped:
            return EXIT_INCOMPLETE
        return EXIT_NO_GO if self.counts[ACCESS_UNRESOLVED] else EXIT_GO

    def line(self) -> str:
        verdict = {EXIT_GO: "GO", EXIT_NO_GO: "NO-GO"}.get(self.exit_code, "INCOMPLETE")
        counts = " ".join(f"{key}={value}" for key, value in self.counts.items())
        stopped = f" stopped={self.stopped!r}" if self.stopped else ""
        return (
            f"RESULT: {verdict} mode={self.mode} population={self.population} "
            f"{counts}{stopped}"
        )


async def run_pass(
    user_ids: Iterable[str],
    *,
    apply: bool,
    population: Population,
    session_factory: SessionFactory,
    adapter: Any,
    pacer: Pacer,
    checkpoint: Checkpoint | None = None,
    output: TextIO | None = None,
    stdout: TextIO | None = None,
    sync: SyncFunction = sync_revenuecat_access,
    max_attempts: int = 3,
    max_wait_seconds: float = 600,
) -> Summary:
    """Reconcile every user once; resumable through ``checkpoint``."""
    summary = Summary("apply" if apply else "dry-run", population.key)
    stdout = stdout or sys.stdout
    completed = checkpoint.completed if checkpoint else set()
    consecutive_failures = 0
    try:
        for user_id in user_ids:
            if user_id in completed:
                summary.counts["skipped"] += 1
                continue
            result = await reconcile_user(
                user_id,
                apply=apply,
                session_factory=session_factory,
                adapter=adapter,
                pacer=pacer,
                sync=sync,
                max_attempts=max_attempts,
                max_wait_seconds=max_wait_seconds,
            )
            summary.add(result)
            if output is not None:
                _json_line(output, asdict(result))
            if result.decision == ACCESS_UNRESOLVED or result.downgrade:
                label = "unresolved" if not result.downgrade else "downgrade"
                print(
                    f"{label} user_id={result.user_id} reason={result.reason} "
                    f"snapshot={result.snapshot} before={result.before} "
                    f"after={result.decision}",
                    file=stdout,
                )
            if result.snapshot == "unavailable" and result.reason in RETRYABLE_REASONS:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_PROVIDER_FAILURES:
                    raise StopRun(
                        f"{consecutive_failures} RevenueCat reads in a row failed "
                        f"(last: {result.reason})"
                    )
                continue  # not completed: --resume reads this user again
            consecutive_failures = 0
            if not result.failed and checkpoint is not None:
                checkpoint.record(user_id)
    except StopRun as stop:
        summary.stopped = str(stop)
    return summary


# --- CLI --------------------------------------------------------------------


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--subscribers",
        action="store_true",
        help="Users with a RevenueCat subscriptions row (default)",
    )
    scope.add_argument("--user-id", help="One database user id")
    scope.add_argument(
        "--repair", action="store_true", help="Rows that need a fresh read now"
    )
    scope.add_argument(
        "--all-users",
        action="store_true",
        help="Every active user; creates RevenueCat customers for non-buyers",
    )
    parser.add_argument(
        "--expiring-within-hours",
        type=float,
        default=None,
        help="--repair: also rows whose expiry or grace deadline is within this "
        "many hours of now, either side (default 24; 0 turns it off)",
    )
    parser.add_argument(
        "--modified-since",
        type=_timestamp,
        help="--repair: also rows updated at or after this ISO-8601 time",
    )
    parser.add_argument("--apply", action="store_true", help="Commit the snapshots")
    parser.add_argument("--output", type=Path, help="Per-user JSONL results")
    parser.add_argument("--checkpoint", type=Path, help="Resumable progress file")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--min-interval-seconds",
        type=float,
        default=1.0,
        help="Minimum time between RevenueCat requests (default 1.0)",
    )
    parser.add_argument(
        "--max-wait-seconds",
        type=float,
        default=600.0,
        help="Stop when RevenueCat asks to wait longer than this (default 600)",
    )
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Only run the SQL go/no-go check (no RevenueCat reads)",
    )
    args = parser.parse_args(argv)
    if not args.repair and (
        args.expiring_within_hours is not None or args.modified_since is not None
    ):
        parser.error("--expiring-within-hours and --modified-since need --repair")
    if args.resume and not args.checkpoint:
        parser.error("--resume needs --checkpoint")
    if args.min_interval_seconds < 0 or args.max_attempts < 1:
        parser.error("--min-interval-seconds >= 0 and --max-attempts >= 1")
    return args


async def gate_counts(session_factory: SessionFactory) -> dict[str, int]:
    """Run GO_NO_GO_SQL: rows that would leave a user unresolved."""
    async with session_factory() as session:
        row = (await session.execute(text(GO_NO_GO_SQL))).one()
        return {"unverified_grants": row[0], "sync_required": row[1]}


def gate_line(counts: dict[str, int]) -> tuple[str, int]:
    verdict = "GO" if not any(counts.values()) else "NO-GO"
    details = " ".join(f"{key}={value}" for key, value in counts.items())
    return f"GATE: {verdict} {details}", EXIT_GO if verdict == "GO" else EXIT_NO_GO


def population_from_args(args: argparse.Namespace) -> Population:
    if args.user_id:
        return Population(POPULATION_USER, user_id=args.user_id)
    if args.repair:
        hours = (
            24.0 if args.expiring_within_hours is None else args.expiring_within_hours
        )
        return Population(
            POPULATION_REPAIR,
            expiring_within_hours=hours,
            modified_since=args.modified_since,
        )
    if args.all_users:
        return Population(POPULATION_ALL_USERS)
    return Population(POPULATION_SUBSCRIBERS)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    population = population_from_args(args)
    mode = "apply" if args.apply else "dry-run"
    now = datetime.now(UTC)

    from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter
    from src.infra.database.config_async import AsyncSessionLocal

    if AsyncSessionLocal is None:
        print("error: the database is not configured", file=sys.stderr)
        return EXIT_USAGE
    if args.gate:
        line, code = gate_line(await gate_counts(AsyncSessionLocal))
        print(line, flush=True)
        return code
    adapter = RevenueCatAdapter()
    if not adapter.api_key:
        print("error: REVENUECAT_SECRET_API_KEY is not set", file=sys.stderr)
        return EXIT_USAGE
    if population.kind == POPULATION_ALL_USERS:
        print(ALL_USERS_WARNING, file=sys.stderr)

    checkpoint: Checkpoint | None = None
    output: TextIO | None = None
    try:
        if args.checkpoint:
            opener = Checkpoint.resume if args.resume else Checkpoint.start
            checkpoint = opener(
                args.checkpoint, mode=mode, population=population, now=now
            )
        if args.output:
            output = _private_append(args.output, truncate=not args.resume)
        user_ids = await candidate_ids(AsyncSessionLocal, population, now)
        print(
            f"mode={mode} population={population.key} candidates={len(user_ids)} "
            f"min_interval={args.min_interval_seconds:g}s",
            flush=True,
        )
        try:
            summary = await run_pass(
                user_ids,
                apply=args.apply,
                population=population,
                session_factory=AsyncSessionLocal,
                adapter=adapter,
                pacer=Pacer(args.min_interval_seconds),
                checkpoint=checkpoint,
                output=output,
                sync=sync_revenuecat_access,
                max_attempts=args.max_attempts,
                max_wait_seconds=args.max_wait_seconds,
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("RESULT: INCOMPLETE stopped='interrupted'", flush=True)
            return EXIT_INCOMPLETE
        except Exception as error:
            # A bug or a database failure: not a NO-GO verdict, an unfinished
            # pass. The checkpoint keeps the users done so far.
            traceback.print_exc()
            print(
                f"RESULT: INCOMPLETE stopped='crashed: {type(error).__name__}'",
                flush=True,
            )
            return EXIT_INCOMPLETE
    except UsageError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE
    finally:
        if checkpoint is not None:
            checkpoint.close()
        if output is not None:
            output.close()
    print(summary.line(), flush=True)
    return summary.exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
