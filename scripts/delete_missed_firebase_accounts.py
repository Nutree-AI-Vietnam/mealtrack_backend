"""Delete Firebase Auth accounts whose queued cleanup never ran.

Account deletion soft-deletes the user and queues ``user.deleted.v1``; the
nutreeai_async worker then deletes the Firebase Auth user. Events that expired
while the worker was down never reached Firebase.

Soft-delete tombstones ``users.firebase_uid``, so the original UIDs of recent
deletions survive only in the "Account deletion completed for firebase_uid"
log lines. Pass them with ``--uids-file`` (``firebase_uid,requested_at`` per
line, ``#`` comments). Inactive rows deleted before the tombstone existed still
hold their UID and are added from the database.

A UID is deleted only when the database shows the deletion happened, no active
account holds the UID (checked again right before the delete), and nobody used
the Firebase account after the request. The script never writes to the
database. Without ``--apply`` it only reports, one JSON line per UID.

Usage:
  python -m scripts.delete_missed_firebase_accounts --uids-file uids.csv \
      --since 2026-09-23 --expect-database HOST/DBNAME \
      --expect-firebase-project PROJECT_ID [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import Select, or_, select

from src.domain.model.user.firebase_identity import TOMBSTONE_PREFIX
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork

# The deletion handler stamps deleted_at a few milliseconds before the log line.
EVIDENCE_WINDOW = timedelta(seconds=10)
# Using the account later than this after the request needs a human decision.
SEEN_AFTER_REQUEST_GRACE = timedelta(minutes=10)
MAX_CONSECUTIVE_ERRORS = 3


class Outcome(StrEnum):
    UNCONFIRMED = "skipped_no_deletion_record"
    ACTIVE_ACCOUNT = "skipped_active_account"
    ALREADY_DELETED = "already_deleted"
    SEEN_AFTER_REQUEST = "review_seen_after_request"
    PENDING = "pending_deletion"
    DELETED = "deleted"
    ERROR = "error"


@dataclass(frozen=True)
class Candidate:
    firebase_uid: str
    requested_at: datetime
    source: str


@dataclass(frozen=True)
class FirebaseAccount:
    uid: str
    last_seen_at: datetime | None
    providers: tuple[str, ...]


def parse_utc(value: str) -> datetime:
    """ISO timestamp; one without an offset is UTC, not the machine's zone."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_uids_file(lines: Iterable[str]) -> list[Candidate]:
    candidates = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        uid, _, requested_at = (part.strip() for part in line.partition(","))
        if (
            not uid
            or not requested_at
            or len(uid) > 128
            or uid.startswith(TOMBSTONE_PREFIX)
            or any(char.isspace() or char in "\"'," for char in uid)
        ):
            raise ValueError(f"Expected firebase_uid,requested_at, got {line!r}")
        candidates.append(Candidate(uid, parse_utc(requested_at), "logs"))
    return candidates


def merge_candidates(*groups: Iterable[Candidate]) -> list[Candidate]:
    """One candidate per UID, keeping the latest deletion request."""
    merged: dict[str, Candidate] = {}
    for group in groups:
        for candidate in group:
            known = merged.get(candidate.firebase_uid)
            if known is None or candidate.requested_at > known.requested_at:
                merged[candidate.firebase_uid] = candidate
    return sorted(merged.values(), key=lambda c: c.requested_at)


def classify(
    candidate: Candidate,
    *,
    deletion_recorded: bool,
    holds_active_account: bool,
    account: FirebaseAccount | None,
    delete_seen_after_request: bool = False,
) -> Outcome:
    if not deletion_recorded:
        return Outcome.UNCONFIRMED
    if holds_active_account:
        return Outcome.ACTIVE_ACCOUNT
    if account is None:
        return Outcome.ALREADY_DELETED
    seen_late = account.last_seen_at is not None and (
        account.last_seen_at > candidate.requested_at + SEEN_AFTER_REQUEST_GRACE
    )
    if seen_late and not delete_seen_after_request:
        return Outcome.SEEN_AFTER_REQUEST
    return Outcome.PENDING


def inactive_candidates_query(since: datetime) -> Select:
    return select(User.firebase_uid, User.deleted_at).where(
        User.is_active.is_(False),
        User.deleted_at >= since,
        User.firebase_uid.not_like(f"{TOMBSTONE_PREFIX}%"),
    )


def deletion_record_query(candidate: Candidate) -> Select:
    """An inactive row still holding the UID, or tombstoned at the request time."""
    return select(User.id).where(
        User.is_active.is_(False),
        User.deleted_at.is_not(None),
        or_(
            User.firebase_uid == candidate.firebase_uid,
            User.firebase_uid.like(f"{TOMBSTONE_PREFIX}%")
            & User.deleted_at.between(
                candidate.requested_at - EVIDENCE_WINDOW,
                candidate.requested_at + EVIDENCE_WINDOW,
            ),
        ),
    )


def active_account_query(uid: str) -> Select:
    return select(User.id).where(User.is_active.is_(True), User.firebase_uid == uid)


async def _exists(query: Select) -> bool:
    async with AsyncUnitOfWork.read_only() as uow:
        return (await uow.session.execute(query.limit(1))).first() is not None


async def deletion_recorded(candidate: Candidate) -> bool:
    return await _exists(deletion_record_query(candidate))


async def holds_active_account(uid: str) -> bool:
    return await _exists(active_account_query(uid))


async def load_database_candidates(since: datetime) -> list[Candidate]:
    async with AsyncUnitOfWork.read_only() as uow:
        rows = await uow.session.execute(inactive_candidates_query(since))
        return [Candidate(uid, deleted_at, "database") for uid, deleted_at in rows]


def check_database_target(expected: str) -> str:
    from src.infra.database.config_async import async_engine

    url = async_engine.url
    actual = f"{url.host}/{url.database}"
    if actual != expected:
        raise SystemExit(f"Database is {actual}, expected {expected}")
    return actual


def initialize_firebase(expected_project: str) -> str:
    """Explicit service account for the expected project; never defaults."""
    import firebase_admin
    from firebase_admin import credentials

    path = os.getenv("FIREBASE_CREDENTIALS")
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if path and os.path.exists(path):
        cred = credentials.Certificate(path)
    elif raw:
        cred = credentials.Certificate(json.loads(raw))
    else:
        raise SystemExit("Set FIREBASE_CREDENTIALS or FIREBASE_SERVICE_ACCOUNT_JSON")
    if cred.project_id != expected_project:
        raise SystemExit(
            f"Service account is for {cred.project_id}, expected {expected_project}"
        )
    app = firebase_admin.initialize_app(cred, {"projectId": expected_project})
    return app.project_id


def lookup_firebase_account(uid: str) -> FirebaseAccount | None:
    from firebase_admin import auth

    try:
        record = auth.get_user(uid)
    except auth.UserNotFoundError:
        return None
    metadata = record.user_metadata
    seen_ms = max(
        metadata.last_refresh_timestamp or 0, metadata.last_sign_in_timestamp or 0
    )
    return FirebaseAccount(
        uid=record.uid,
        last_seen_at=datetime.fromtimestamp(seen_ms / 1000, UTC) if seen_ms else None,
        providers=tuple(p.provider_id for p in record.provider_data),
    )


def delete_firebase_account(uid: str) -> None:
    from src.infra.services.firebase_auth_service import FirebaseAuthService

    FirebaseAuthService.delete_firebase_user(uid)


async def run(
    candidates: list[Candidate],
    *,
    apply: bool,
    emit: Callable[[dict[str, object]], None],
    delete_seen_after_request: bool = False,
    recorded: Callable[[Candidate], Awaitable[bool]] = deletion_recorded,
    active: Callable[[str], Awaitable[bool]] = holds_active_account,
    lookup: Callable[[str], FirebaseAccount | None] = lookup_firebase_account,
    delete: Callable[[str], None] = delete_firebase_account,
) -> Counter[str]:
    """Decides each UID in turn and emits its row before moving on."""
    counts: Counter[str] = Counter()
    consecutive_errors = 0
    for candidate in candidates:
        row: dict[str, object] = {
            "firebase_uid": candidate.firebase_uid,
            "source": candidate.source,
            "requested_at": candidate.requested_at.isoformat(),
        }
        try:
            is_recorded = await recorded(candidate)
            account = lookup(candidate.firebase_uid) if is_recorded else None
            if account:
                row["last_seen_at"] = (
                    account.last_seen_at.isoformat() if account.last_seen_at else None
                )
                row["providers"] = list(account.providers)
            # Last read before the delete: a re-registration wins any race.
            is_active = await active(candidate.firebase_uid)
            outcome = classify(
                candidate,
                deletion_recorded=is_recorded,
                holds_active_account=is_active,
                account=account,
                delete_seen_after_request=delete_seen_after_request,
            )
            if outcome is Outcome.PENDING and apply:
                delete(candidate.firebase_uid)
                outcome = Outcome.DELETED
            consecutive_errors = 0
        except Exception as error:  # noqa: BLE001 - recorded per UID, then bounded
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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uids-file", required=True)
    parser.add_argument("--since", required=True, type=parse_utc)
    parser.add_argument("--expect-database", required=True, help="HOST/DBNAME")
    parser.add_argument("--expect-firebase-project", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--delete-seen-after-request",
        action="store_true",
        help="Also delete accounts used after the request (after review)",
    )
    args = parser.parse_args()

    database = check_database_target(args.expect_database)
    project = initialize_firebase(args.expect_firebase_project)
    with open(args.uids_file, encoding="utf-8") as handle:
        from_logs = parse_uids_file(handle)
    candidates = merge_candidates(from_logs, await load_database_candidates(args.since))
    print(
        json.dumps(
            {
                "database": database,
                "firebase_project": project,
                "apply": args.apply,
                "candidates": len(candidates),
            }
        ),
        flush=True,
    )

    counts = await run(
        candidates,
        apply=args.apply,
        delete_seen_after_request=args.delete_seen_after_request,
        emit=lambda row: print(json.dumps(row), flush=True),
    )
    print(json.dumps({"apply": args.apply, "counts": dict(counts)}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
